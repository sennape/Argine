// Argine — lettore dell'expert store e gestore di residenza.
// Vedi argine-store.h per il perche'. Qui c'e' il come.

#include "argine-store.h"
#include "argine-research.h"
#include "argine-retention-policy.h"

#include <cstdio>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cerrno>
#include <cstdint>
#include <cstring>
#include <deque>
#include <type_traits>
#include <list>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

#if defined(_WIN32)
#    ifndef WIN32_LEAN_AND_MEAN
#        define WIN32_LEAN_AND_MEAN
#    endif
#    include <windows.h>
#else
#    include <fcntl.h>
#    include <sys/mman.h>
#    include <unistd.h>
#endif

namespace {

std::atomic<unsigned> g_read_delay_us{ 0 };
// Guasti di lettura iniettati, per i test. Serve perche' il percorso "una slice
// del blocco non arriva" non si raggiunge altrimenti, e senza di esso il
// contratto tutto-o-niente resterebbe affermato e non verificato.
std::atomic<unsigned> g_fail_reads{ 0 };

// Consuma un guasto iniettato, se ce n'e' uno in coda. Sta qui e non dentro un
// singolo percorso di lettura perche' i percorsi sono due — il blocco e
// l'emetti-e-continua — e un'iniezione che ne copre uno solo rende verde un test
// che non prova niente. E' successo: il test "una lettura mancata viene riportata"
// passava perche' il guasto non arrivava mai al percorso nuovo.
bool guasto_iniettato() {
    unsigned q = g_fail_reads.load(std::memory_order_relaxed);
    while (q > 0) {
        if (g_fail_reads.compare_exchange_weak(q, q - 1)) {
            return true;
        }
    }
    return false;
}
std::atomic<int>      g_pretouch{ 0 };

constexpr size_t kMaxHandles = 64;  // un descrittore per thread, con un tetto

size_t round_up(size_t n, size_t a) {
    if (a == 0) {
        return n;
    }
    const size_t r = n % a;
    return r ? n + (a - r) : n;
}

size_t round_down(size_t n, size_t a) {
    return a ? n - (n % a) : n;
}

size_t page_size() {
#if defined(_WIN32)
    static size_t ps = [] {
        SYSTEM_INFO si;
        GetSystemInfo(&si);
        return (size_t) si.dwPageSize;
    }();
#else
    static size_t ps = (size_t) sysconf(_SC_PAGESIZE);
#endif
    return ps;
}

}  // namespace

// ===========================================================================
// store
// ===========================================================================

struct psx_store {
    std::string path;
    std::string err;
    int         direct = 0;
    int         direct_granted = 0;  // se l'I/O non bufferizzato e' stato concesso

    uint32_t alignment  = 0;
    uint32_t n_layers   = 0;
    uint32_t n_experts  = 0;
    uint64_t data_off   = 0;
    uint64_t total      = 0;
    uint64_t model_size = 0;

    std::vector<uint32_t>          layers;
    std::vector<int>               row_of;   // layer del modello -> riga, -1 se assente
    std::vector<struct psx_entry>  entries;  // riga * n_experts + expert

    // un descrittore per thread: su un handle sincrono la lettura posizionata
    // non usa il puntatore condiviso, ma tenerne uno per thread evita ogni
    // contesa sul file object
    std::mutex mtx;
#if defined(_WIN32)
    std::vector<HANDLE> handles;
#else
    std::vector<int> handles;
#endif
    std::vector<unsigned char> handle_busy;

    void fail(const std::string & m) { err = m; }
};

namespace {

bool read_at(psx_store * s, uint64_t off, void * dst, size_t bytes);

// Apre un nuovo descrittore sul file, con o senza cache.
#if defined(_WIN32)
HANDLE open_handle(const std::string & path, int direct) {
    // FILE_FLAG_OVERLAPPED serve per avere piu' letture in volo sullo stesso
    // descrittore: e' la condizione della lettura in blocco, e il guadagno
    // misurato sta tutto li' (a coda 2,49 il dispositivo da' 2552 MB/s, a 7,5 ne
    // da' 3522). Con questo flag ReadFile puo' tornare ERROR_IO_PENDING, quindi
    // anche la lettura singola deve saper aspettare: vedi read_with.
    DWORD flags = (direct ? FILE_FLAG_NO_BUFFERING : FILE_FLAG_RANDOM_ACCESS)
                  | FILE_FLAG_OVERLAPPED;
    return CreateFileA(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, flags,
                       nullptr);
}
bool handle_ok(HANDLE h) {
    return h != INVALID_HANDLE_VALUE && h != nullptr;
}
#else
int open_handle(const std::string & path, int direct) {
    int flags = O_RDONLY;
#    if defined(O_DIRECT)
    if (direct) {
        flags |= O_DIRECT;
    }
#    else
    (void) direct;
#    endif
    return ::open(path.c_str(), flags);
}
bool handle_ok(int fd) {
    return fd >= 0;
}
#endif

// Un descrittore preso dal pool: indice per restituirlo, **valore copiato** per
// usarlo.
//
// La copia non e' un vezzo. La prima versione passava l'indice a read_with, che
// dereferenziava s->handles[idx] fuori dal lock: un altro thread che apriva un
// nuovo descrittore faceva crescere il vettore, la riallocazione spostava la
// memoria sotto i piedi del lettore. ThreadSanitizer l'ha segnalata come corsa
// in _M_realloc_insert; dal vivo sarebbe stato un use-after-free raro e
// inspiegabile. Copiando il valore sotto il lock il problema non esiste.
struct Borrowed {
    int index;
#if defined(_WIN32)
    HANDLE h;
#else
    int h;
#endif
};

Borrowed acquire_handle(psx_store * s) {
    std::lock_guard<std::mutex> lk(s->mtx);
    for (size_t i = 0; i < s->handles.size(); i++) {
        if (!s->handle_busy[i]) {
            s->handle_busy[i] = 1;
            return Borrowed{ (int) i, s->handles[i] };
        }
    }
#if defined(_WIN32)
    const Borrowed nulla{ -1, INVALID_HANDLE_VALUE };
#else
    const Borrowed nulla{ -1, -1 };
#endif
    if (s->handles.size() >= kMaxHandles) {
        return nulla;
    }
    auto h = open_handle(s->path, s->direct);
    if (!handle_ok(h)) {
        return nulla;
    }
    s->handles.push_back(h);
    s->handle_busy.push_back(1);
    return Borrowed{ (int) s->handles.size() - 1, h };
}

void release_handle(psx_store * s, int idx) {
    if (idx < 0) {
        return;
    }
    std::lock_guard<std::mutex> lk(s->mtx);
    s->handle_busy[idx] = 0;
}

// Lettura posizionata su un descrittore preso in prestito.
#if defined(_WIN32)
size_t read_with(HANDLE h, uint64_t off, void * dst, size_t bytes) {
#else
size_t read_with(int h, uint64_t off, void * dst, size_t bytes) {
#endif
#if defined(_WIN32)
    // Gli handle sono aperti con FILE_FLAG_OVERLAPPED, quindi ReadFile puo'
    // tornare prima che i byte ci siano: senza l'attesa qui, chi chiama leggerebbe
    // memoria non ancora riempita — lo stesso guasto che la cache aveva nella sua
    // prima versione, ma un livello piu' in basso e invisibile ai test della
    // cache. L'evento e' thread_local e riusato: una CreateEvent per lettura
    // sarebbe migliaia al secondo.
    static thread_local HANDLE ev_singolo = nullptr;
    if (!ev_singolo) {
        ev_singolo = CreateEventA(nullptr, TRUE, FALSE, nullptr);
    }
    OVERLAPPED ov;
    memset(&ov, 0, sizeof(ov));
    ov.Offset     = (DWORD) (off & 0xFFFFFFFFull);
    ov.OffsetHigh = (DWORD) (off >> 32);
    ov.hEvent     = ev_singolo;
    if (ev_singolo) {
        ResetEvent(ev_singolo);
    }
    DWORD got = 0;
    if (!ReadFile(h, dst, (DWORD) bytes, &got, &ov)) {
        const DWORD e = GetLastError();
        if (e == ERROR_IO_PENDING) {
            if (!GetOverlappedResult(h, &ov, &got, TRUE)) {
                return GetLastError() == ERROR_HANDLE_EOF ? (size_t) got : 0;
            }
        } else if (e != ERROR_HANDLE_EOF) {
            return 0;
        }
    }
    return (size_t) got;
#else
    ssize_t got = ::pread(h, dst, bytes, (off_t) off);
    return got < 0 ? 0 : (size_t) got;
#endif
}

bool read_at(psx_store * s, uint64_t off, void * dst, size_t bytes) {
    const Borrowed b = acquire_handle(s);
    if (b.index < 0) {
        s->fail("nessun descrittore disponibile per lo store");
        return false;
    }
    const size_t got = read_with(b.h, off, dst, bytes);
    release_handle(s, b.index);
    return got == bytes;
}

}  // namespace

struct psx_store * psx_open(const char * path, int direct) {
    auto * s  = new psx_store();
    s->path   = path ? path : "";
    s->direct = direct ? 1 : 0;

    // l'intestazione e le tabelle si leggono con la cache attiva: sono piccole
    // e non allineate, e non vale la pena vincolarle al settore
    auto h = open_handle(s->path, 0);
    if (!handle_ok(h)) {
        s->fail("impossibile aprire lo store: " + s->path);
        return s;
    }
    s->handles.push_back(h);
    s->handle_busy.push_back(0);

    unsigned char head[PSX_HEADER_BYTES];
    if (!read_at(s, 0, head, sizeof(head))) {
        s->fail("intestazione illeggibile: file troppo corto?");
        return s;
    }

    auto u32 = [&](size_t o) {
        uint32_t v;
        memcpy(&v, head + o, 4);
        return v;
    };
    auto u64 = [&](size_t o) {
        uint64_t v;
        memcpy(&v, head + o, 8);
        return v;
    };

    if (u32(0) != PSX_MAGIC) {
        s->fail("non e' un expert store di Argine");
        return s;
    }
    const uint32_t version = u32(4);
    if (version != PSX_VERSION) {
        char buf[160];
        snprintf(buf, sizeof(buf),
                 "store in versione %u, questa build legge la %u: ricostruiscilo con "
                 "`argine store build`",
                 version, PSX_VERSION);
        s->fail(buf);
        return s;
    }
    s->alignment = u32(12);
    s->n_layers  = u32(16);
    s->n_experts = u32(20);
    const uint32_t n_kinds = u32(24);
    if (n_kinds != PSX_N_KINDS) {
        s->fail("numero di tensori per expert inatteso");
        return s;
    }
    s->data_off              = u64(32);
    s->total                 = u64(40);
    const uint64_t layer_tab = u64(48);
    const uint64_t group_tab = u64(56);
    s->model_size            = u64(64);

    if (s->alignment == 0 || (s->alignment & (s->alignment - 1)) != 0) {
        s->fail("allineamento dichiarato non e' una potenza di due");
        return s;
    }
    if (s->n_layers == 0 || s->n_experts == 0) {
        s->fail("store vuoto");
        return s;
    }

    s->layers.resize(s->n_layers);
    if (!read_at(s, layer_tab, s->layers.data(), (size_t) s->n_layers * 4)) {
        s->fail("tabella dei layer illeggibile");
        return s;
    }
    uint32_t max_layer = 0;
    for (uint32_t l : s->layers) {
        max_layer = std::max(max_layer, l);
    }
    s->row_of.assign((size_t) max_layer + 1, -1);
    for (uint32_t i = 0; i < s->n_layers; i++) {
        s->row_of[s->layers[i]] = (int) i;
    }

    const size_t n = (size_t) s->n_layers * s->n_experts;
    s->entries.resize(n);
    // la tabella su disco e' 36 byte per voce: 3 x u64 e 3 x u32, compattata.
    // psx_entry ha lo stesso contenuto ma il compilatore potrebbe allinearla a
    // 40, quindi si legge in un buffer e si copia campo per campo invece di
    // fidarsi del layout.
    std::vector<unsigned char> raw(n * 36);
    if (!read_at(s, group_tab, raw.data(), raw.size())) {
        s->fail("tabella delle slice illeggibile");
        return s;
    }
    for (size_t i = 0; i < n; i++) {
        const unsigned char * p = raw.data() + i * 36;
        for (int k = 0; k < (int) PSX_N_KINDS; k++) {
            memcpy(&s->entries[i].offset[k], p + k * 8, 8);
            memcpy(&s->entries[i].size[k], p + 24 + k * 4, 4);
        }
        // un offset disallineato renderebbe impossibile l'I/O non bufferizzato:
        // meglio accorgersene qui che al primo token
        for (int k = 0; k < (int) PSX_N_KINDS; k++) {
            if (s->entries[i].offset[k] % s->alignment != 0) {
                char buf[192];
                snprintf(buf, sizeof(buf),
                         "slice %zu tensore %d: offset %llu non allineato a %u; lo store e' "
                         "stato scritto da una versione incompatibile",
                         i, k, (unsigned long long) s->entries[i].offset[k], s->alignment);
                s->fail(buf);
                return s;
            }
        }
    }

    // da qui in avanti le letture vere: si riapre il pool col flag richiesto
#if defined(_WIN32)
    CloseHandle(s->handles[0]);
#else
    ::close(s->handles[0]);
#endif
    s->handles.clear();
    s->handle_busy.clear();
    auto h2 = open_handle(s->path, s->direct);
    if (!handle_ok(h2)) {
        s->fail("impossibile riaprire lo store per le letture");
        return s;
    }
    s->handles.push_back(h2);
    s->handle_busy.push_back(0);
    s->err.clear();

    // Prova di lettura. Serve perche' l'I/O non bufferizzato non e' sempre
    // disponibile: certi filesystem (overlay, rete) e certi dispositivi lo
    // rifiutano, e scoprirlo al primo token significa morire a meta'
    // caricamento. Meglio accorgersene qui e ripiegare, dicendolo.
    if (s->direct && !s->entries.empty()) {
        const size_t prova = round_up(s->entries[0].size[0], s->alignment);
        void *       buf   = nullptr;
#if defined(_WIN32)
        buf = VirtualAlloc(nullptr, prova, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
#else
        buf = mmap(nullptr, prova, PROT_READ | PROT_WRITE,
                   MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (buf == MAP_FAILED) {
            buf = nullptr;
        }
#endif
        bool ok = false;
        if (buf) {
            const Borrowed b = acquire_handle(s);
            if (b.index >= 0) {
                ok = read_with(b.h, s->entries[0].offset[0], buf, prova) == prova;
                release_handle(s, b.index);
            }
#if defined(_WIN32)
            VirtualFree(buf, 0, MEM_RELEASE);
#else
            munmap(buf, prova);
#endif
        }
        if (!ok) {
            s->direct_granted = 0;
            s->direct         = 0;
            for (auto hh : s->handles) {
#if defined(_WIN32)
                CloseHandle(hh);
#else
                ::close(hh);
#endif
            }
            s->handles.clear();
            s->handle_busy.clear();
            auto h3 = open_handle(s->path, 0);
            if (!handle_ok(h3)) {
                s->fail("lo store non si apre nemmeno con la cache dell'OS attiva");
                return s;
            }
            s->handles.push_back(h3);
            s->handle_busy.push_back(0);
        } else {
            s->direct_granted = 1;
        }
    }
    return s;
}

void * psx_alloc_aligned(const struct psx_store * s, size_t bytes) {
    const size_t a = s && s->alignment ? s->alignment : page_size();
    const size_t n = round_up(bytes, a);
#if defined(_WIN32)
    return VirtualAlloc(nullptr, n, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
#else
    void * p = mmap(nullptr, n, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    return p == MAP_FAILED ? nullptr : p;
#endif
}

void psx_free_aligned(void * p, size_t bytes) {
    if (!p) {
        return;
    }
#if defined(_WIN32)
    (void) bytes;
    VirtualFree(p, 0, MEM_RELEASE);
#else
    munmap(p, round_up(bytes, page_size()));
#endif
}

int psx_direct(const struct psx_store * s) {
    return s ? s->direct_granted : 0;
}

void psx_close(struct psx_store * s) {
    if (!s) {
        return;
    }
    for (auto h : s->handles) {
#if defined(_WIN32)
        if (handle_ok(h)) {
            CloseHandle(h);
        }
#else
        if (handle_ok(h)) {
            ::close(h);
        }
#endif
    }
    delete s;
}

const char * psx_error(const struct psx_store * s) {
    if (!s || s->err.empty()) {
        return nullptr;
    }
    return s->err.c_str();
}

uint32_t psx_n_layers(const struct psx_store * s) { return s ? s->n_layers : 0; }
uint32_t psx_n_experts(const struct psx_store * s) { return s ? s->n_experts : 0; }
uint32_t psx_alignment(const struct psx_store * s) { return s ? s->alignment : 0; }
uint64_t psx_total_bytes(const struct psx_store * s) { return s ? s->total : 0; }
uint64_t psx_model_bytes(const struct psx_store * s) { return s ? s->model_size : 0; }

int psx_layer_row(const struct psx_store * s, uint32_t layer) {
    if (!s || layer >= s->row_of.size()) {
        return -1;
    }
    return s->row_of[layer];
}

const struct psx_entry * psx_get(const struct psx_store * s, uint32_t layer, uint32_t expert) {
    const int row = psx_layer_row(s, layer);
    if (row < 0 || expert >= s->n_experts) {
        return nullptr;
    }
    return &s->entries[(size_t) row * s->n_experts + expert];
}

size_t psx_slice_read_bytes(const struct psx_store * s, uint32_t layer, uint32_t expert,
                            enum psx_kind kind) {
    const struct psx_entry * e = psx_get(s, layer, expert);
    if (!e) {
        return 0;
    }
    return round_up(e->size[kind], s->alignment);
}

size_t psx_read_slice(struct psx_store * s, uint32_t layer, uint32_t expert, enum psx_kind kind,
                      void * dst) {
    const struct psx_entry * e = psx_get(s, layer, expert);
    if (!e || !dst) {
        return 0;
    }
    // con l'I/O non bufferizzato la lunghezza deve essere multipla del settore:
    // si legge anche la coda di allineamento, che nello store e' a zero
    const size_t want = s->direct ? round_up(e->size[kind], s->alignment) : e->size[kind];
    const Borrowed b = acquire_handle(s);
    if (b.index < 0) {
        s->fail("nessun descrittore disponibile");
        return 0;
    }
    const size_t got = read_with(b.h, e->offset[kind], dst, want);
#if defined(_WIN32)
    const unsigned long codice = got < e->size[kind] ? GetLastError() : 0;
#else
    const int codice = got < e->size[kind] ? errno : 0;
#endif
    release_handle(s, b.index);
    if (got < e->size[kind]) {
        char buf[224];
        snprintf(buf, sizeof(buf),
                 "lettura di layer %u expert %u tensore %d fallita: %zu byte su %zu, "
                 "errore %ld%s",
                 layer, expert, (int) kind, got, want, (long) codice,
                 s->direct ? " (con la cache dell'OS scavalcata: il dispositivo o il "
                             "filesystem potrebbe non accettare letture non bufferizzate)"
                           : "");
        s->fail(buf);
        return 0;
    }
    return e->size[kind];
}

// ===========================================================================
// regione: indirizzi riservati, memoria impegnata a pezzi
// ===========================================================================

struct psx_region {
    unsigned char * base      = nullptr;
    size_t          bytes     = 0;
    size_t          slot      = 0;
    size_t          n_slots   = 0;
    size_t          committed = 0;
    std::mutex      mtx;
};

struct psx_region * psx_region_new(size_t bytes, size_t slot_bytes, size_t n_slots) {
    if (bytes == 0) {
        return nullptr;
    }
    auto * r    = new psx_region();
    r->bytes    = round_up(bytes, page_size());
    r->slot     = slot_bytes;
    r->n_slots  = n_slots;

#if defined(_WIN32)
    // MEM_RESERVE prende gli indirizzi senza toccare la RAM: su 64 bit
    // riservare 20 GiB non costa nulla, e il puntatore resta contiguo per tutto
    // il tensore, che e' l'unica cosa che ggml pretende
    void * p = VirtualAlloc(nullptr, r->bytes, MEM_RESERVE, PAGE_NOACCESS);
#else
    // PROT_NONE + MAP_NORESERVE e' l'equivalente: indirizzi sì, pagine no
    void * p = mmap(nullptr, r->bytes, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (p == MAP_FAILED) {
        p = nullptr;
    }
#endif
    if (!p) {
        delete r;
        return nullptr;
    }
    r->base = (unsigned char *) p;
    return r;
}

void psx_region_free(struct psx_region * r) {
    if (!r) {
        return;
    }
    if (r->base) {
#if defined(_WIN32)
        VirtualFree(r->base, 0, MEM_RELEASE);
#else
        munmap(r->base, r->bytes);
#endif
    }
    delete r;
}

void * psx_region_base(const struct psx_region * r) {
    return r ? (void *) r->base : nullptr;
}

size_t psx_region_committed(const struct psx_region * r) {
    return r ? r->committed : 0;
}

int psx_region_commit(struct psx_region * r, size_t offset, size_t bytes) {
    if (!r || !r->base || bytes == 0) {
        return 0;
    }
    const size_t ps  = page_size();
    const size_t beg = round_down(offset, ps);
    const size_t end = std::min(round_up(offset + bytes, ps), r->bytes);
    if (beg >= end) {
        return 0;
    }
#if defined(_WIN32)
    if (!VirtualAlloc(r->base + beg, end - beg, MEM_COMMIT, PAGE_READWRITE)) {
        return 0;
    }
#else
    if (mprotect(r->base + beg, end - beg, PROT_READ | PROT_WRITE) != 0) {
        return 0;
    }
#endif
    std::lock_guard<std::mutex> lk(r->mtx);
    r->committed += end - beg;
    return 1;
}

int psx_region_decommit(struct psx_region * r, size_t offset, size_t bytes) {
    if (!r || !r->base || bytes == 0) {
        return 0;
    }
    const size_t ps  = page_size();
    const size_t beg = round_up(offset, ps);
    const size_t end = std::min(round_down(offset + bytes, ps), r->bytes);
    if (beg >= end) {
        return 0;
    }
#if defined(_WIN32)
    // MEM_DECOMMIT restituisce la RAM ma tiene gli indirizzi: il puntatore di
    // ggml continua a essere valido, e un accesso a una slice sfrattata
    // fallisce in modo rumoroso invece di leggere spazzatura
    if (!VirtualFree(r->base + beg, end - beg, MEM_DECOMMIT)) {
        return 0;
    }
#else
    if (mmap(r->base + beg, end - beg, PROT_NONE,
             MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED | MAP_NORESERVE, -1, 0) == MAP_FAILED) {
        return 0;
    }
#endif
    std::lock_guard<std::mutex> lk(r->mtx);
    r->committed -= std::min(r->committed, end - beg);
    return 1;
}

// ===========================================================================
// cache LRU con budget rigido
// ===========================================================================

namespace {

struct CacheKey {
    const void * region;
    size_t       offset;
    bool         operator==(const CacheKey & o) const {
        return region == o.region && offset == o.offset;
    }
};

// Mescolatore a 64 bit (finalizzatore di MurmurHash3). Serve perche' i bit
// BASSI della chiave non hanno entropia: gli offset delle slice sono multipli
// di 4096 e la base della regione e' allineata a 64 KiB, quindi i quattro bit
// bassi sono zero in entrambi.
//
// La prima versione faceva hash(puntatore) ^ (offset * primo) e poi il modulo
// del numero di settori. Con sedici settori, modulo 16 di un valore con i bit
// bassi a zero fa sempre zero: TUTTE le chiavi finivano nel settore 0, la cache
// usava 310 MiB su 4982 dichiarati, gli hit crollavano dal 94% al 67% e la
// generazione dimezzava. Un hash va bene per una mappa (che usa i bit alti) e
// pessimo per una divisione in settori: sono due usi diversi dello stesso
// numero, e questo mescolatore serve il secondo.
inline uint64_t mix64(uint64_t x) {
    x ^= x >> 33;
    x *= 0xff51afd7ed558ccdull;
    x ^= x >> 33;
    x *= 0xc4ceb9fe1a85ec53ull;
    x ^= x >> 33;
    return x;
}

struct CacheKeyHash {
    size_t operator()(const CacheKey & k) const {
        return (size_t) mix64((uint64_t) (uintptr_t) k.region * 0x9e3779b97f4a7c15ull
                              + (uint64_t) k.offset);
    }
};

}  // namespace

// La cache e' divisa in SETTORI indipendenti, ciascuno col proprio mutex.
//
// Con un mutex solo, ogni accesso — anche un hit, che nel carico vero e' il 94%
// dei casi — passava per lo stesso lucchetto: 1.4 milioni di prese su otto
// thread, e la misura diceva che li' se ne andava il 16-18% del tempo di I/O.
// Un hit non legge niente dal disco: e' una ricerca in una mappa e uno
// spostamento in testa a una lista, e non ha senso che otto thread si mettano
// in fila per farlo.
//
// Ogni settore ha il suo budget (totale / numero di settori), quindi il tetto
// complessivo resta rigido: e' la somma di tetti rigidi. Lo sfratto diventa LRU
// dentro il settore invece che globale, il che e' leggermente meno efficiente
// in teoria; con chiavi distribuite uniformemente la differenza si perde nel
// rumore, mentre la contesa tolta si misura.
struct Shard {
    std::mutex              mtx;
    std::condition_variable cv;
    uint64_t                budget   = 0;
    uint64_t                resident = 0;
    uint64_t                hits = 0, misses = 0, evictions = 0, bytes_read = 0;

    struct Slot {
        struct psx_region * region;
        size_t              offset;
        size_t              bytes;
        bool                ready;  // false = un thread la sta ancora leggendo
        unsigned            pins;   // > 0 = qualcuno la sta usando: non sfrattabile
        // Argine2 research metadata: only read when ARGINE_RESEARCH_TRACE is
        // active, so that evictions can be attributed to (layer, expert, kind)
        // and resident durations measured. They never influence a decision.
        uint32_t            layer     = 0;
        uint32_t            expert    = 0;
        uint32_t            kind      = 0;
        uint64_t            loaded_ns = 0;
        uint64_t            last_ns   = 0;
        uint64_t            uses      = 0;
    };
    std::list<Slot>                                                   order;  // fronte = recente
    std::unordered_map<CacheKey, std::list<Slot>::iterator, CacheKeyHash> index;
    // Argine2 research: index of this shard in psx_cache::shards, and the
    // per-shard sequence number of LRU state changes (incremented under mtx).
    unsigned id  = 0;
    uint64_t seq = 0;
};

struct psx_cache {
    uint64_t budget = 0;
    std::vector<std::unique_ptr<Shard>> shards;
    // i tempi restano globali: sono somme, non hanno contesa
    std::atomic<uint64_t> ns_commit{ 0 }, ns_read{ 0 }, ns_decommit{ 0 };
    std::atomic<uint64_t> blocchi{ 0 }, letture{ 0 };
    std::atomic<uint64_t> ns_attesa{ 0 }, ns_lock{ 0 }, ns_fault{ 0 };
    // Argine2: hard-budget invariant monitor. Counts admissions after which a
    // shard's resident bytes exceeded its budget. By construction it must stay
    // 0; it is exported so that every measurement can prove it, not assume it.
    std::atomic<uint64_t> violazioni{ 0 };

    // --- R2-12: politica di retention ---------------------------------------
    //
    // Presente solo quando ARGINE_RETENTION_POLICY e' stata letta e validata.
    // Con una politica attiva questa cache ha esattamente UN settore
    // (vedi psx_cache_attach_retention_policy), e psx_cache_ensure_many
    // sostituisce per intero la selezione della vittima del settore 0 con la
    // decisione di `policy`, sotto lo stesso mutex del settore (shards[0]->mtx):
    // nessun nuovo lock globale, si riusa quello che gia' serializza ogni
    // accesso a quel settore.
    std::unique_ptr<argine_retention::RetentionPolicy> policy;
    std::string policy_id;              // "b_selected" | "predicted_protect"
    std::string policy_rejection;       // messaggio statico per error_out se INVALID
    uint32_t    policy_step = 0;        // un passo per evento (una chiamata a ensure_many)
    uint32_t    policy_events = 0;      // eventi effettivamente serviti dalla politica
    FILE *      policy_trace_fp = nullptr;  // ARGINE_RETENTION_TRACE, JSONL, o nullptr
    // Oggetto (layer, expert) -> slice (CacheKey) attualmente residenti per
    // quell'oggetto in questo settore. Serve a tradurre una vittima scelta
    // dalla politica (un Obj logico) nelle fino a tre slice fisiche
    // (gate/up/down) da sfrattare insieme, e viceversa per registrare cosa e'
    // stato ammesso. Esiste solo quando `policy` e' attivo.
    std::unordered_map<uint64_t, std::vector<CacheKey>> policy_obj_slices;

    static uint64_t policy_obj_key(uint32_t layer, uint32_t expert) {
        return ((uint64_t) layer << 32) | (uint64_t) expert;
    }

    Shard & shard_of(const CacheKey & k) {
        return *shards[CacheKeyHash()(k) % shards.size()];
    }
};

struct psx_cache * psx_cache_new(uint64_t budget_bytes) {
    return psx_cache_new_n(budget_bytes, 0);
}

struct psx_cache * psx_cache_new_n(uint64_t budget_bytes, unsigned shards) {
    auto * c  = new psx_cache();
    c->budget = budget_bytes;

    // Quanti settori: abbastanza da togliere la contesa sul mutex, non tanti da
    // scatenare quella sul lock dello spazio di indirizzamento del processo,
    // dove finiscono commit e decommit. Il punto di equilibrio dipende dalla
    // macchina e si trova misurando.
    size_t n = shards ? (size_t) shards : 16;
    if (!shards && budget_bytes) {
        const uint64_t minimo_per_settore = 32ull << 20;  // 32 MiB: ~11 slice
        n = (size_t) (budget_bytes / minimo_per_settore);
        if (n < 1) {
            n = 1;
        }
        if (n > 16) {
            n = 16;
        }
    }
    if (n < 1) {
        n = 1;
    }
    c->shards.reserve(n);
    for (size_t i = 0; i < n; i++) {
        c->shards.emplace_back(new Shard());
        c->shards.back()->id = (unsigned) i;
        // l'ultimo settore assorbe il resto della divisione: la somma dei
        // budget dei settori e' esattamente il budget richiesto
        c->shards.back()->budget = budget_bytes ? budget_bytes / n : 0;
    }
    if (budget_bytes && n > 0) {
        c->shards.back()->budget += budget_bytes - (budget_bytes / n) * n;
    }
    return c;
}

void psx_cache_free(struct psx_cache * c) {
    delete c;
}

unsigned psx_cache_shards(const struct psx_cache * c) {
    return c ? (unsigned) c->shards.size() : 0;
}

unsigned psx_debug_shard_index(const struct psx_cache * c, struct psx_region * region,
                               size_t offset) {
    if (!c || c->shards.empty()) {
        return 0;
    }
    return (unsigned) (CacheKeyHash()(CacheKey{ region, offset }) % c->shards.size());
}

void psx_cache_shard_load(const struct psx_cache * c, uint64_t * out, unsigned n) {
    if (!c || !out) {
        return;
    }
    for (unsigned i = 0; i < n && i < c->shards.size(); i++) {
        std::lock_guard<std::mutex> lk(c->shards[i]->mtx);
        out[i] = c->shards[i]->resident;
    }
}

void psx_cache_get_stats(const struct psx_cache * c, struct psx_cache_stats * out) {
    if (!c || !out) {
        return;
    }
    memset(out, 0, sizeof(*out));
    for (const auto & sh : c->shards) {
        std::lock_guard<std::mutex> lk(sh->mtx);
        out->hits           += sh->hits;
        out->misses         += sh->misses;
        out->evictions      += sh->evictions;
        out->bytes_read     += sh->bytes_read;
        out->bytes_resident += sh->resident;
        out->budget         += sh->budget;
    }
    out->ns_commit   = c->ns_commit.load();
    out->ns_read     = c->ns_read.load();
    out->ns_decommit = c->ns_decommit.load();
    out->ns_attesa   = c->ns_attesa.load();
    out->ns_lock     = c->ns_lock.load();
    out->ns_fault    = c->ns_fault.load();
    out->blocchi     = c->blocchi.load();
    out->letture     = c->letture.load();
    out->violazioni  = c->violazioni.load();
}

// ===========================================================================
// R2-12: integrazione politica di retention nel percorso reale
// ===========================================================================
//
// Niente di questo blocco cambia il comportamento di default (nessuna
// politica richiesta): psx_cache_ensure_many prende la strada esistente,
// invariata, quando c->policy e' nullo.

int psx_retention_policy_requested(void) {
    const char * v = getenv("ARGINE_RETENTION_POLICY");
    return (v && v[0] != '\0') ? 1 : 0;
}

int psx_cache_has_retention_policy(const struct psx_cache * c) {
    return (c && c->policy) ? 1 : 0;
}

int psx_cache_attach_retention_policy(struct psx_cache * c, const char ** error_out) {
    if (error_out) {
        *error_out = nullptr;
    }
    if (!c) {
        if (error_out) *error_out = "psx_cache_attach_retention_policy: cache nulla";
        return 0;
    }

    argine_retention::RetentionConfig cfg = argine_retention::parse_retention_config_from_env();

    if (cfg.kind == argine_retention::PolicyKind::NONE) {
        // Nessuna politica richiesta: nessun cambiamento di comportamento.
        return 1;
    }

    if (cfg.kind == argine_retention::PolicyKind::INVALID) {
        c->policy_rejection = cfg.rejection_reason.empty()
            ? "ARGINE_RETENTION_POLICY: configurazione non valida, nessun motivo riportato"
            : cfg.rejection_reason;
        if (error_out) {
            *error_out = c->policy_rejection.c_str();
        }
        return 0;  // fail-closed: il chiamante NON deve ricadere su NONE/B_selected
    }

    // Vincolo architetturale (vedi commento sul campo `policy` in struct
    // psx_cache): l'unita' di decisione della politica e' l'oggetto (layer,
    // expert), non la singola slice, e un evento viene servito in un'unica
    // sezione critica. Con piu' di un settore lo stesso oggetto potrebbe avere
    // le sue tre slice (gate/up/down) sparse su mutex diversi, e due eventi su
    // settori diversi potrebbero decidere in parallelo su stato (resident_,
    // t_) che la classe RetentionPolicy non protegge da sola: deve farlo UN
    // solo settore, UN solo mutex.
    if (c->shards.size() != 1) {
        c->policy_rejection =
            "ARGINE_RETENTION_POLICY richiede esattamente un settore "
            "(ARGINE_EXPERT_SHARDS=1); il chiamante deve forzarlo PRIMA di "
            "creare la cache quando psx_retention_policy_requested() torna vero.";
        if (error_out) {
            *error_out = c->policy_rejection.c_str();
        }
        return 0;
    }

    if (cfg.kind == argine_retention::PolicyKind::B_SELECTED) {
        c->policy = std::make_unique<argine_retention::BSelectedPolicy>(cfg.base);
        c->policy_id = "b_selected";
    } else {
        c->policy = std::make_unique<argine_retention::PredictedProtectPolicy>(
            cfg.base, cfg.predictor_decay, cfg.top_k, cfg.n_experts);
        c->policy_id = "predicted_protect";
    }
    c->policy->start(c->budget);

    // ARGINE_RETENTION_TRACE: istrumentazione opzionale, deterministica,
    // disattivata di default. Un JSONL, una riga per decisione di sfratto/
    // ammissione a livello di evento. Nessun timestamp a parete negli stessi
    // campi usati per il confronto deterministico (si vedano i commenti dove
    // viene scritta la riga).
    if (const char * trace_path = getenv("ARGINE_RETENTION_TRACE")) {
        if (trace_path[0] != '\0') {
            c->policy_trace_fp = fopen(trace_path, "w");
            if (!c->policy_trace_fp) {
                c->policy_rejection = "ARGINE_RETENTION_TRACE: impossibile aprire il file per la scrittura";
                if (error_out) {
                    *error_out = c->policy_rejection.c_str();
                }
                c->policy.reset();
                return 0;
            }
        }
    }

    return 1;
}

void psx_cache_get_retention_stats(const struct psx_cache * c, struct psx_retention_stats * out) {
    if (!out) {
        return;
    }
    memset(out, 0, sizeof(*out));
    if (!c || !c->policy) {
        return;
    }
    // shards[0]->mtx: stesso mutex sotto cui la politica decide, cosi' queste
    // statistiche non sono mai lette a meta' di un evento.
    std::lock_guard<std::mutex> lk(c->shards[0]->mtx);
    const argine_retention::RetentionStats s = c->policy->stats();
    snprintf(out->policy_id, sizeof(out->policy_id), "%s", c->policy_id.c_str());
    out->decay_factor          = s.decay_factor;
    out->aging_interval_steps  = s.aging_interval_steps;
    out->predictor_decay       = s.predictor_decay;
    out->hits                  = s.hits;
    out->misses                = s.misses;
    out->evictions             = s.evictions;
    out->resident_bytes        = s.resident_bytes;
    out->predictor_decisions   = s.predictor_decisions;
    out->protected_fallbacks   = s.protected_fallbacks;
    out->events_served         = c->policy_events;
}

void psx_cache_unpin(struct psx_cache * c, struct psx_region * region, size_t offset) {
    if (!c || !region) {
        return;
    }
    const CacheKey key{ region, offset };
    Shard &        sh = c->shard_of(key);
    {
        std::lock_guard<std::mutex> lk(sh.mtx);
        auto it = sh.index.find(key);
        if (it == sh.index.end() || it->second->pins == 0) {
            return;
        }
        it->second->pins--;
        if (it->second->pins > 0) {
            return;
        }
    }
    // una slice si e' liberata: chi aspettava spazio in questo settore puo' riprovare
    sh.cv.notify_all();
}

uint64_t psx_cache_min_budget(const struct psx_store * s, uint32_t n_expert_per_op) {
    if (!s || s->entries.empty()) {
        return 0;
    }
    // la piu' grande combinazione di slice che una singola operazione tiene
    // bloccate insieme: n expert x tutti i tensori
    uint64_t per_expert = 0;
    for (int k = 0; k < (int) PSX_N_KINDS; k++) {
        per_expert += round_up(s->entries[0].size[k], s->alignment);
    }
    const uint32_t n = n_expert_per_op ? n_expert_per_op : 1;
    return per_expert * n;
}

void psx_cache_touch(struct psx_cache * c, struct psx_region * region, size_t offset) {
    if (!c || !region) {
        return;
    }
    const CacheKey key{ region, offset };
    Shard &        sh = c->shard_of(key);
    std::lock_guard<std::mutex> lk(sh.mtx);
    auto it = sh.index.find(key);
    if (it != sh.index.end()) {
        sh.order.splice(sh.order.begin(), sh.order, it->second);
    }
}

namespace {

// Una slice prenotata: lo spazio e' impegnato e lo slot inserito nell'indice in
// stato "in caricamento", ma i byte non sono ancora arrivati.
//
// Esiste per separare le due fasi che prima erano una sola funzione: prendere il
// posto (sotto lock) e riempirlo (fuori dal lock). La separazione serve a poter
// prenotare *piu'* slice e poi leggerle tutte insieme, perche' la misura dice
// che a byte totali fissi la profondita' di coda rende piu' della dimensione
// della lettura: alla coda 2,49 di oggi il dispositivo da' 2552 MB/s, a coda 7,5
// ne da' 3522.
struct Prenotazione {
    enum Stato { ERRORE, HIT, DA_LEGGERE };
    Stato                            stato = ERRORE;
    Shard *                          sh    = nullptr;
    std::list<Shard::Slot>::iterator mia{};
    struct psx_region *              region = nullptr;
    size_t                           offset = 0;
    size_t                           need   = 0;
    unsigned char *                  dst    = nullptr;
    uint64_t                         file_off = 0;
    size_t                           want   = 0;  // byte da chiedere al disco
    size_t                           utili  = 0;  // byte che contano come letti
    uint32_t                         layer = 0, expert = 0;
    enum psx_kind                    kind  = PSX_GATE;
    // Argine2 research: did this access wait for a read issued by another
    // thread (outcome 'w' instead of 'h')? Set only on the HIT path.
    bool                             atteso_altro = false;
    // Argine2 research: owning shard and the per-shard sequence number of the
    // decisive state change (hit -> front, or insertion for a miss).
    unsigned                         shard_id = 0;
    uint64_t                         seq      = 0;
};

Prenotazione prenota(struct psx_cache * c, struct psx_store * s, struct psx_region * region,
                     size_t offset, uint32_t layer, uint32_t expert, enum psx_kind kind) {
    Prenotazione pr;
    pr.region = region;
    pr.offset = offset;
    pr.layer  = layer;
    pr.expert = expert;
    pr.kind   = kind;
    // Una slice ha tre stati, non due: assente, **in caricamento**, pronta.
    //
    // Ignorare quello in mezzo e' il bug che questo codice ha avuto nella prima
    // versione: la slice veniva annunciata nell'indice e solo dopo letta, quindi
    // un altro thread trovava un hit e usava memoria non ancora riempita. Con
    // MUL_MAT_ID, che tutti i thread eseguono sullo stesso tensore, succedeva a
    // ogni miss. Con la finestra allargata a 300 us il test ne contava 430 su
    // 480 tentativi: il modello avrebbe sbagliato senza dire niente.
    //
    // Chi trova una slice in caricamento aspetta che sia pronta. Chi sfratta non
    // tocca ne' le slice in caricamento ne' quelle bloccate.
    if (!c || !s || !region) {
        return pr;
    }
    const struct psx_entry * e = psx_get(s, layer, expert);
    if (!e) {
        return pr;
    }
    const size_t   need = psx_slice_read_bytes(s, layer, expert, kind);
    const CacheKey key{ region, offset };
    Shard &        sh   = c->shard_of(key);

    using orologio = std::chrono::steady_clock;
    auto ns_da = [](orologio::time_point t0) {
        return (uint64_t) std::chrono::duration_cast<std::chrono::nanoseconds>(
                   orologio::now() - t0).count();
    };

    std::list<Shard::Slot>::iterator mia;
    {
        const auto t_lock = orologio::now();
        std::unique_lock<std::mutex> lk(sh.mtx);
        c->ns_lock.fetch_add(ns_da(t_lock), std::memory_order_relaxed);
        int attese = 0;

        // Ciclo esterno: ogni volta che si rilascia il mutex si **riparte dalla
        // ricerca**. Non e' pedanteria: la prima versione decideva "questa
        // slice e' assente", poi aspettava spazio con wait_for (che rilascia il
        // lock) e riprendeva da dopo la decisione. Nel frattempo un altro
        // thread inseriva la stessa chiave, e al risveglio si creava un secondo
        // slot per lo stesso offset, con gli unpin che finivano sul nodo
        // sbagliato e memoria ancora in uso che veniva sfrattata.
        for (;;) {
            auto it = sh.index.find(key);
            if (it != sh.index.end()) {
                if (it->second->ready) {
                    it->second->pins++;
                    pr.shard_id = sh.id;
                    pr.seq      = sh.seq++;
                    if (arx_enabled()) {
                        it->second->last_ns = arx_now_ns();
                        it->second->uses++;
                    }
                    sh.order.splice(sh.order.begin(), sh.order, it->second);
                    sh.hits++;
                    pr.stato = Prenotazione::HIT;
                    return pr;
                }
                // la sta leggendo un altro: aspetto il suo annuncio e ricontrollo
                pr.atteso_altro = true;
                const auto t_att = orologio::now();
                sh.cv.wait(lk);
                c->ns_attesa.fetch_add(ns_da(t_att), std::memory_order_relaxed);
                continue;
            }

            // Assente: faccio spazio. Il budget e' rigido per scelta, perche' e'
            // l'unico modo di rispondere "quanto grande su 16 GiB" con un numero
            // invece che con una speranza.
            bool riparti = false;
            while (sh.budget > 0 && sh.resident + need > sh.budget) {
                bool     sfrattato = false;
                uint32_t saltati   = 0;  // Argine2: pinned / loading slots passed over
                for (auto v = sh.order.end(); v != sh.order.begin();) {
                    --v;
                    if (!v->ready || v->pins > 0) {
                        saltati++;
                        continue;  // ci sta scrivendo o la sta usando qualcuno
                    }
                    {
                        const uint64_t sq = sh.seq++;
                        if (arx_enabled()) {
                            arx_evict(v->layer, v->expert, v->kind, v->bytes, v->loaded_ns,
                                      v->last_ns, v->uses, sh.id, sq, saltati);
                        }
                    }
                    const auto t_dec = orologio::now();
                    psx_region_decommit(v->region, v->offset, v->bytes);
                    c->ns_decommit.fetch_add(ns_da(t_dec), std::memory_order_relaxed);
                    sh.resident -= std::min<uint64_t>(sh.resident, v->bytes);
                    sh.index.erase(CacheKey{ v->region, v->offset });
                    sh.order.erase(v);
                    sh.evictions++;
                    sfrattato = true;
                    break;
                }
                if (sfrattato) {
                    continue;
                }
                // niente di sfrattabile: se il settore e' vuoto, il suo budget e'
                // piu' piccolo di una slice e non cambiera' mai; altrimenti sto
                // aspettando che qualcuno sblocchi.
                if (sh.order.empty() || ++attese > 2000) {
                    sh.misses++;
                    return pr;
                }
                const auto t_att2 = orologio::now();
                sh.cv.wait_for(lk, std::chrono::milliseconds(1));
                c->ns_attesa.fetch_add(ns_da(t_att2), std::memory_order_relaxed);
                riparti = true;  // ho mollato il lock: lo stato non vale piu'
                break;
            }
            if (riparti) {
                continue;
            }

            const auto t_com = orologio::now();
            const int  ok_commit = psx_region_commit(region, offset, need);
            c->ns_commit.fetch_add(ns_da(t_com), std::memory_order_relaxed);
            if (!ok_commit) {
                sh.misses++;
                return pr;
            }
            sh.misses++;
            sh.resident += need;
            if (sh.budget > 0 && sh.resident > sh.budget) {
                c->violazioni.fetch_add(1, std::memory_order_relaxed);
            }
            sh.order.push_front(Shard::Slot{ region, offset, need, false, 1 });
            pr.shard_id    = sh.id;
            pr.seq         = sh.seq++;
            mia            = sh.order.begin();
            mia->layer     = layer;
            mia->expert    = expert;
            mia->kind      = (uint32_t) kind;
            sh.index[key]  = mia;
            break;
        }
    }

    pr.stato = Prenotazione::DA_LEGGERE;
    pr.sh    = &sh;
    pr.mia   = mia;
    pr.need  = need;
    pr.dst   = (unsigned char *) psx_region_base(region) + offset;
    pr.file_off = e->offset[kind];
    pr.want  = s->direct ? round_up(e->size[kind], s->alignment) : e->size[kind];
    pr.utili = e->size[kind];
    return pr;
}

// Chiude una prenotazione: annuncia la slice come pronta, o disfa tutto.
//
// Il percorso di fallimento deve rimettere esattamente lo stato di prima —
// residenza, indice, lista, memoria impegnata — altrimenti un errore di lettura
// lascia uno slot fantasma che nessuno sfrattera' mai.
void completa(struct psx_cache * c, const Prenotazione & pr, bool ok) {
    (void) c;
    Shard & sh = *pr.sh;
    {
        std::lock_guard<std::mutex> lk(sh.mtx);
        if (ok) {
            pr.mia->ready = true;
            if (arx_enabled()) {
                pr.mia->loaded_ns = arx_now_ns();
                pr.mia->last_ns   = pr.mia->loaded_ns;
                pr.mia->uses      = 1;
            }
            sh.bytes_read += pr.need;
        } else {
            {
                const uint64_t sq = sh.seq++;
                if (arx_enabled()) {
                    arx_undo(pr.layer, pr.expert, (uint32_t) pr.kind, pr.mia->bytes, sh.id, sq);
                }
            }
            sh.resident -= std::min<uint64_t>(sh.resident, pr.mia->bytes);
            sh.index.erase(CacheKey{ pr.region, pr.offset });
            sh.order.erase(pr.mia);
            psx_region_decommit(pr.region, pr.offset, pr.need);
        }
    }
    // sveglio chi aspettava questa slice, sia per usarla sia per riprovarci
    sh.cv.notify_all();
}

}  // namespace

// Legge n slice gia' prenotate, tutte in volo insieme dove il sistema lo permette.
//
// Su Windows gli handle sono aperti con FILE_FLAG_OVERLAPPED, quindi un solo
// descrittore accetta piu' richieste contemporanee: si emettono tutte e si
// aspetta la fine di tutte. E' questa la ragione d'essere del blocco — non la
// dimensione delle letture, che a byte totali fissi si scambia con la coda.
//
// Su POSIX non c'e' un equivalente portabile di pari semplicita' (servirebbe
// io_uring o aio), quindi si leggono in sequenza: il risultato e' corretto e la
// contabilita' identica, manca solo il guadagno. Il bersaglio del progetto e'
// Windows, e qui vale piu' un codice che non mente che un guadagno su una
// piattaforma che non usiamo.
static size_t psx_read_many(struct psx_store * s, size_t n, const uint64_t * offsets,
                     void * const * dsts, const size_t * sizes, unsigned char * ok_out) {
    if (!s || n == 0) {
        return 0;
    }
    size_t riuscite = 0;
#if defined(_WIN32)
    const Borrowed b = acquire_handle(s);
    if (b.index < 0) {
        s->fail("nessun descrittore disponibile");
        for (size_t i = 0; i < n; i++) {
            ok_out[i] = 0;
        }
        return 0;
    }
    // Un evento per richiesta. Sono thread_local e riusati: crearli a ogni nodo
    // MoE significherebbe migliaia di CreateEvent al secondo.
    static thread_local std::vector<HANDLE> eventi;
    std::vector<OVERLAPPED> ov(n);
    std::vector<unsigned char> emessa(n, 0);
    while (eventi.size() < n) {
        HANDLE ev = CreateEventA(nullptr, TRUE, FALSE, nullptr);
        if (!ev) {
            break;
        }
        eventi.push_back(ev);
    }
    for (size_t i = 0; i < n; i++) {
        ok_out[i] = 0;
        if (i >= eventi.size()) {
            break;  // senza evento non si puo' attendere: resta da leggere in coda
        }
        memset(&ov[i], 0, sizeof(OVERLAPPED));
        ov[i].Offset     = (DWORD) (offsets[i] & 0xFFFFFFFFull);
        ov[i].OffsetHigh = (DWORD) (offsets[i] >> 32);
        ov[i].hEvent     = eventi[i];
        ResetEvent(eventi[i]);
        DWORD got = 0;
        if (ReadFile(b.h, dsts[i], (DWORD) sizes[i], &got, &ov[i])) {
            emessa[i] = 2;  // completata subito
        } else if (GetLastError() == ERROR_IO_PENDING) {
            emessa[i] = 1;  // in volo
        } else {
            emessa[i] = 0;
        }
    }
    for (size_t i = 0; i < n; i++) {
        if (emessa[i] == 0) {
            continue;
        }
        DWORD got = 0;
        if (GetOverlappedResult(b.h, &ov[i], &got, TRUE) && got >= sizes[i]) {
            ok_out[i] = 1;
            riuscite++;
        }
    }
    release_handle(s, b.index);
    // Le richieste senza evento (tetto raggiunto) si leggono in modo sincrono:
    // meglio piu' lente che non lette.
    for (size_t i = 0; i < n; i++) {
        if (!ok_out[i] && i >= eventi.size()) {
            if (read_at(s, offsets[i], dsts[i], sizes[i])) {
                ok_out[i] = 1;
                riuscite++;
            }
        }
    }
#else
    for (size_t i = 0; i < n; i++) {
        ok_out[i] = read_at(s, offsets[i], dsts[i], sizes[i]) ? 1 : 0;
        riuscite += ok_out[i];
    }
#endif
    // Guasto iniettato: si applica all'ULTIMA richiesta del blocco, cosi' le
    // precedenti sono riuscite davvero ed e' il loro sblocco che viene messo alla
    // prova.
    if (guasto_iniettato() && ok_out[n - 1]) {
        ok_out[n - 1] = 0;
        riuscite--;
    }
    return riuscite;
}

int psx_cache_ensure(struct psx_cache * c, struct psx_store * s, struct psx_region * region,
                     size_t offset, uint32_t layer, uint32_t expert, enum psx_kind kind) {
    return psx_cache_ensure_many(c, s, 1, &region, &offset, &layer, &expert, &kind);
}

// ===========================================================================
// R2-12: un intero evento MoE (un layer, le sue slice mancanti) servito
// attraverso la politica di retention collegata a `c`. Vedi il commento sul
// campo `policy` in struct psx_cache per il vincolo di un solo settore, e
// argine-retention-policy.h per il nucleo deterministico riusato qui, non
// riscritto: questa funzione traduce le sue decisioni (Obj logici) in
// CacheKey fisiche, non ridecide nulla sulla matematica congelata.
static int servi_evento_con_politica(struct psx_cache * c, struct psx_store * s, size_t n,
                                      struct psx_region * const * regions, const size_t * offsets,
                                      const uint32_t * layers, const uint32_t * experts,
                                      const enum psx_kind * kinds) {
    if (c->shards.size() != 1) {
        s->fail("servi_evento_con_politica: la cache non ha un solo settore");
        return 0;
    }
    Shard & sh = *c->shards[0];

    // Il router decide una volta per layer (argine-experts.cpp): un blocco
    // con layer misti sotto una politica attiva e' un errore del chiamante,
    // non un caso da gestire in silenzio.
    const uint32_t layer = layers[0];
    for (size_t i = 1; i < n; i++) {
        if (layers[i] != layer) {
            s->fail("servi_evento_con_politica: blocco con layer misti sotto una politica di retention");
            return 0;
        }
    }

    using orologio = std::chrono::steady_clock;
    auto ns_da = [](orologio::time_point t0) {
        return (uint64_t) std::chrono::duration_cast<std::chrono::nanoseconds>(
                   orologio::now() - t0).count();
    };

    std::lock_guard<std::mutex> lk(sh.mtx);

    std::vector<uint32_t> dedup_experts(experts, experts + n);
    std::sort(dedup_experts.begin(), dedup_experts.end());
    dedup_experts.erase(std::unique(dedup_experts.begin(), dedup_experts.end()), dedup_experts.end());

    // bytes_of_layer: somma dei byte sui kind DISTINTI realmente presenti in
    // QUESTO evento per questo layer (non un fisso "sempre tre": un kind puo'
    // mancare perche' il suo tensore e' finito sul percorso normale, vedi
    // argine-experts.cpp). Adattamento minimo di interfaccia, non una
    // modifica della matematica congelata.
    bool     seen_kind[PSX_N_KINDS] = { false, false, false };
    uint64_t bytes_of_layer         = 0;
    for (size_t i = 0; i < n; i++) {
        const unsigned k = (unsigned) kinds[i];
        if (k < PSX_N_KINDS && !seen_kind[k]) {
            seen_kind[k] = true;
            bytes_of_layer += psx_slice_read_bytes(s, layers[i], experts[i], kinds[i]);
        }
    }

    const uint32_t step = c->policy_step++;
    argine_retention::EventOutcome outcome =
        c->policy->access(step, layer, dedup_experts, bytes_of_layer);
    c->policy_events++;

    if (outcome.overflow_events > 0 || outcome.overflow_misses > 0) {
        // L'evento non entra nel budget nemmeno vuotando tutto. Conservativo
        // per questa fase: fallisce in modo osservabile invece di ammettere
        // solo una parte in silenzio (nessuna evidenza reale lo esercita
        // ancora; vedi il report finale).
        s->fail("retention policy: evento non servibile nemmeno dopo aver sfrattato tutto (overflow)");
        return 0;
    }

    // Sfratta ogni oggetto scelto come vittima PRIMA di ammettere le nuove
    // slice: traduce l'Obj logico (layer, expert) nelle sue slice fisiche
    // davvero residenti in questo settore (fino a tre: gate/up/down),
    // tracciate in c->policy_obj_slices, non assunte.
    for (const argine_retention::Obj & victim : outcome.evicted) {
        const uint64_t  ok_key = psx_cache::policy_obj_key(victim.layer, victim.expert);
        auto             it_obj = c->policy_obj_slices.find(ok_key);
        if (it_obj == c->policy_obj_slices.end()) {
            char buf[192];
            snprintf(buf, sizeof(buf),
                     "retention policy: sfratto di (layer=%u, expert=%u) richiesto ma nessuna slice "
                     "fisica indicizzata per quell'oggetto in questo settore",
                     victim.layer, victim.expert);
            s->fail(buf);
            return 0;
        }
        for (const CacheKey & key : it_obj->second) {
            auto it_slot = sh.index.find(key);
            if (it_slot == sh.index.end()) {
                continue;  // difensivo: gia' non residente
            }
            auto slot_it = it_slot->second;
            if (!slot_it->ready || slot_it->pins > 0) {
                char buf[192];
                snprintf(buf, sizeof(buf),
                         "retention policy: sfratto di (layer=%u, expert=%u) ma una sua slice e' in "
                         "caricamento o bloccata (pins=%u) -- invariante di residenza violato",
                         victim.layer, victim.expert, slot_it->pins);
                s->fail(buf);
                return 0;
            }
            const auto t_dec = orologio::now();
            psx_region_decommit(slot_it->region, slot_it->offset, slot_it->bytes);
            c->ns_decommit.fetch_add(ns_da(t_dec), std::memory_order_relaxed);
            sh.resident -= std::min<uint64_t>(sh.resident, slot_it->bytes);
            sh.order.erase(slot_it);
            sh.index.erase(it_slot);
            sh.evictions++;
            if (c->policy_trace_fp) {
                fprintf(c->policy_trace_fp,
                        "{\"step\":%u,\"decision\":\"evict\",\"policy\":\"%s\",\"layer\":%u,"
                        "\"expert\":%u,\"resident_bytes_after\":%llu}\n",
                        step, c->policy_id.c_str(), victim.layer, victim.expert,
                        (unsigned long long) sh.resident);
            }
        }
        c->policy_obj_slices.erase(it_obj);
    }
    if (c->policy_trace_fp) {
        fflush(c->policy_trace_fp);
    }

    // missed_set: quali expert di QUESTO evento vanno effettivamente letti da
    // disco (non erano gia' residenti). Tutto cio' che e' in dedup_experts ma
    // non in missed_set e' un hit: la politica lo considera gia' residente.
    std::vector<uint8_t> is_missed(dedup_experts.size(), 0);
    {
        size_t mi = 0;
        for (size_t di = 0; di < dedup_experts.size() && mi < outcome.missed.size(); di++) {
            if (dedup_experts[di] == outcome.missed[mi]) {
                is_missed[di] = 1;
                mi++;
            }
        }
    }
    auto expert_is_missed = [&](uint32_t expert) -> bool {
        auto it = std::lower_bound(dedup_experts.begin(), dedup_experts.end(), expert);
        if (it == dedup_experts.end() || *it != expert) {
            return false;
        }
        return is_missed[(size_t) (it - dedup_experts.begin())] != 0;
    };

    // Fase 1 (sotto lo stesso lock, niente rilascio fra "prendere posto" e
    // "leggere": vedi il commento sopra questa funzione per il perche'):
    // hit -> pin + splice in testa; miss -> impegna la memoria.
    struct DaLeggere {
        size_t                            i;
        struct psx_region *               region;
        size_t                            offset;
        uint32_t                          layer, expert;
        enum psx_kind                     kind;
        size_t                            need;
        unsigned char *                   dst;
        uint64_t                          file_off;
        size_t                            want;
        size_t                            utili;
    };
    std::vector<DaLeggere> da_leggere;
    da_leggere.reserve(n);

    for (size_t i = 0; i < n; i++) {
        const CacheKey key{ regions[i], offsets[i] };
        auto            it = sh.index.find(key);
        if (it != sh.index.end()) {
            // Hit: deve esserlo, la politica pensa che l'oggetto sia residente.
            it->second->pins++;
            sh.order.splice(sh.order.begin(), sh.order, it->second);
            sh.hits++;
            continue;
        }
        if (!expert_is_missed(experts[i])) {
            char buf[192];
            snprintf(buf, sizeof(buf),
                     "retention policy: (layer=%u, expert=%u, kind=%u) non e' nell'indice ma la "
                     "politica non lo elenca come mancante -- incoerenza fra stato logico e fisico",
                     layers[i], experts[i], (unsigned) kinds[i]);
            s->fail(buf);
            return 0;
        }
        const struct psx_entry * e = psx_get(s, layers[i], experts[i]);
        if (!e) {
            s->fail("retention policy: psx_get ha restituito null per una slice da leggere");
            return 0;
        }
        const size_t need = psx_slice_read_bytes(s, layers[i], experts[i], kinds[i]);
        const auto   t_com = orologio::now();
        const int    ok_commit = psx_region_commit(regions[i], offsets[i], need);
        c->ns_commit.fetch_add(ns_da(t_com), std::memory_order_relaxed);
        if (!ok_commit) {
            s->fail("retention policy: psx_region_commit fallito dopo lo sfratto deciso dalla politica");
            return 0;
        }
        sh.misses++;
        sh.resident += need;
        if (sh.budget > 0 && sh.resident > sh.budget) {
            c->violazioni.fetch_add(1, std::memory_order_relaxed);
        }
        sh.order.push_front(Shard::Slot{ regions[i], offsets[i], need, false, 1 });
        auto mia       = sh.order.begin();
        mia->layer     = layers[i];
        mia->expert    = experts[i];
        mia->kind      = (uint32_t) kinds[i];
        sh.index[key]  = mia;
        sh.seq++;

        DaLeggere d;
        d.i        = i;
        d.region   = regions[i];
        d.offset   = offsets[i];
        d.layer    = layers[i];
        d.expert   = experts[i];
        d.kind     = kinds[i];
        d.need     = need;
        d.dst      = (unsigned char *) psx_region_base(regions[i]) + offsets[i];
        d.file_off = e->offset[kinds[i]];
        d.want     = s->direct ? round_up(e->size[kinds[i]], s->alignment) : e->size[kinds[i]];
        d.utili    = e->size[kinds[i]];
        da_leggere.push_back(d);
    }

    // Fase 2: leggere, ancora sotto lock (vedi commento sopra: nessun altro
    // thread puo' osservare "in caricamento" perche' nessun altro accesso a
    // questo settore e' concorrente mentre lo teniamo).
    if (!da_leggere.empty()) {
        std::vector<uint64_t>      off(da_leggere.size());
        std::vector<void *>        dst(da_leggere.size());
        std::vector<size_t>        len(da_leggere.size());
        std::vector<unsigned char> ok(da_leggere.size(), 0);
        for (size_t i = 0; i < da_leggere.size(); i++) {
            off[i] = da_leggere[i].file_off;
            dst[i] = da_leggere[i].dst;
            len[i] = da_leggere[i].want;
        }
        const auto t_read = orologio::now();
        psx_read_many(s, da_leggere.size(), off.data(), dst.data(), len.data(), ok.data());
        c->ns_read.fetch_add(ns_da(t_read), std::memory_order_relaxed);
        c->blocchi.fetch_add(1, std::memory_order_relaxed);
        c->letture.fetch_add(da_leggere.size(), std::memory_order_relaxed);

        bool tutte = true;
        for (size_t i = 0; i < da_leggere.size(); i++) {
            const DaLeggere & d   = da_leggere[i];
            const CacheKey    key{ d.region, d.offset };
            auto              it = sh.index.find(key);
            if (it == sh.index.end()) {
                tutte = false;
                continue;
            }
            if (ok[i]) {
                it->second->ready = true;
                sh.bytes_read += d.need;
            } else {
                tutte = false;
                // Disfa esattamente come completa(..., false): rimette lo
                // stato com'era prima della prenotazione.
                psx_region_decommit(d.region, d.offset, d.need);
                sh.resident -= std::min<uint64_t>(sh.resident, d.need);
                sh.order.erase(it->second);
                sh.index.erase(it);
            }
        }
        if (!tutte) {
            s->fail("retention policy: lettura di una slice fallita dentro un evento");
            return 0;
        }
    }

    // Registra, per ogni expert di questo evento, le sue slice come
    // residenti per l'oggetto (layer, expert) -- indipendentemente da hit o
    // miss -- cosi' un futuro sfratto di questo oggetto trova tutte le sue
    // slice fisiche, non solo quella appena letta.
    for (size_t i = 0; i < n; i++) {
        const uint64_t ok_key = psx_cache::policy_obj_key(layers[i], experts[i]);
        auto &          vec   = c->policy_obj_slices[ok_key];
        const CacheKey  key{ regions[i], offsets[i] };
        if (std::find(vec.begin(), vec.end(), key) == vec.end()) {
            vec.push_back(key);
        }
    }

    return 1;
}

int psx_cache_ensure_many(struct psx_cache * c, struct psx_store * s,
                          size_t n, struct psx_region * const * regions, const size_t * offsets,
                          const uint32_t * layers, const uint32_t * experts,
                          const enum psx_kind * kinds) {
    if (!c || !s || n == 0) {
        return 0;
    }
    if (c->policy) {
        return servi_evento_con_politica(c, s, n, regions, offsets, layers, experts, kinds);
    }
    using orologio = std::chrono::steady_clock;
    auto ns_da = [](orologio::time_point t0) {
        return (uint64_t) std::chrono::duration_cast<std::chrono::nanoseconds>(
                   orologio::now() - t0).count();
    };

    // Un blocco che non ci sta nel budget *per intero* non ci starà mai: le sue
    // slice restano bloccate fino alla fine del blocco, quindi devono essere
    // residenti tutte insieme. Senza questo controllo ogni tentativo spendeva
    // 2000 attese da un millisecondo prima di arrendersi — due secondi per
    // scoprire una cosa calcolabile subito. Visto dal vivo come uno stallo, non
    // come un errore, che e' il modo peggiore di fallire.
    {
        uint64_t somma = 0;
        for (size_t i = 0; i < n; i++) {
            somma += psx_slice_read_bytes(s, layers[i], experts[i], kinds[i]);
        }
        if (c->budget > 0 && somma > c->budget) {
            char buf[192];
            snprintf(buf, sizeof(buf),
                     "blocco di %zu slice: %llu byte da tenere insieme, budget %llu. "
                     "Serve almeno psx_cache_min_budget per operazione.",
                     n, (unsigned long long) somma, (unsigned long long) c->budget);
            s->fail(buf);
            return 0;
        }
    }

    std::vector<Prenotazione> pren;
    pren.reserve(n);
    const bool            arx = arx_enabled() != 0;
    std::vector<uint64_t> req_ids;  // Argine2: one id per issued read
    // Fase 1: prendere posto per tutte. Una prenotazione fallita ferma il blocco,
    // ma quelle gia' prese vanno comunque chiuse, altrimenti restano "in
    // caricamento" per sempre e chi le aspetta non si sveglia mai.
    for (size_t i = 0; i < n; i++) {
        Prenotazione p = prenota(c, s, regions[i], offsets[i], layers[i], experts[i], kinds[i]);
        if (p.stato == Prenotazione::ERRORE) {
            for (auto & q : pren) {
                if (q.stato == Prenotazione::DA_LEGGERE) {
                    completa(c, q, false);
                }
            }
            return 0;
        }
        if (p.stato == Prenotazione::DA_LEGGERE) {
            pren.push_back(p);
            if (arx) {
                const uint64_t rid = arx_next_request_id();
                req_ids.push_back(rid);
                arx_access(layers[i], experts[i], (uint32_t) kinds[i], 'm', p.utili, rid,
                           p.shard_id, p.seq);
            }
        } else if (arx) {
            arx_access(layers[i], experts[i], (uint32_t) kinds[i], p.atteso_altro ? 'w' : 'h',
                       psx_slice_read_bytes(s, layers[i], experts[i], kinds[i]), 0, p.shard_id,
                       p.seq);
        }
    }
    if (pren.empty()) {
        return 1;  // tutte hit
    }

    // Fase 2: leggere. Il ritardo artificiale e il pretouch restano qui, prima
    // delle letture, perche' servono a misurare proprio questo punto.
    if (const unsigned d = g_read_delay_us.load()) {
        std::this_thread::sleep_for(std::chrono::microseconds(d));
    }
    if (g_pretouch.load(std::memory_order_relaxed)) {
        // forza qui i page fault delle pagine appena impegnate, invece di
        // pagarli dentro il DMA dove finiscono contati come tempo di lettura
        const auto t_fault = orologio::now();
        for (auto & p : pren) {
            memset(p.dst, 0, p.need);
        }
        c->ns_fault.fetch_add(ns_da(t_fault), std::memory_order_relaxed);
    }

    std::vector<uint64_t>      off(pren.size());
    std::vector<void *>        dst(pren.size());
    std::vector<size_t>        len(pren.size());
    std::vector<unsigned char> ok(pren.size(), 0);
    for (size_t i = 0; i < pren.size(); i++) {
        off[i] = pren[i].file_off;
        dst[i] = pren[i].dst;
        len[i] = pren[i].want;
    }
    // ATTENZIONE alla semantica di ns_read: con le letture sovrapposte questo
    // tempo e' il *parete del blocco*, non la somma dei tempi delle singole
    // letture. Dividendolo per i miss si ottiene un numero piu' piccolo del
    // costo vero di una lettura, e dividendolo per il parete totale si ottiene
    // una profondita' di coda piu' piccola del vero.
    //
    // E' un errore che ho commesso: ho cambiato il codice e il significato della
    // metrica nello stesso passo, e la misura che ne e' uscita non poteva dire se
    // l'ottimizzazione funzionava. Per questo si contano anche i blocchi e le
    // letture: la coda effettiva e' (ns_read/parete) x (letture/blocchi).
    const auto     t_read    = orologio::now();
    const uint64_t arx_issue = arx ? arx_now_ns() : 0;
    psx_read_many(s, pren.size(), off.data(), dst.data(), len.data(), ok.data());
    c->ns_read.fetch_add(ns_da(t_read), std::memory_order_relaxed);
    if (arx) {
        // Blocking path: the thread waits from issue to completion, nothing is
        // hidden. With more than one read in the block the individual
        // completions are not observed, only the end of the block: the
        // completion time is then an upper bound and is flagged as such.
        const uint64_t fine  = arx_now_ns();
        const uint32_t flags = ARX_FLAG_SYNCHRONOUS |
                               (pren.size() == 1 ? ARX_FLAG_COMPLETE_EXACT : ARX_FLAG_COMPLETE_BOUND);
        for (size_t i = 0; i < pren.size(); i++) {
            arx_io(req_ids[i], pren[i].layer, pren[i].expert, (uint32_t) pren[i].kind, pren[i].utili,
                   arx_issue, fine, arx_issue, fine, ok[i] != 0, flags);
        }
    }
    c->blocchi.fetch_add(1, std::memory_order_relaxed);
    c->letture.fetch_add(pren.size(), std::memory_order_relaxed);

    // Fase 3: annunciare o disfare, una per una.
    int tutte = 1;
    for (size_t i = 0; i < pren.size(); i++) {
        completa(c, pren[i], ok[i] != 0);
        if (!ok[i]) {
            tutte = 0;
        }
    }
    if (!tutte) {
        // Tutto o niente. Le slice lette bene sono rimaste bloccate dalla
        // prenotazione, e chi chiama non ha modo di sapere quali: lasciargliele
        // significherebbe pin che nessuno sblocca e budget occupato per sempre.
        // La prima versione di questa funzione lo faceva, e lo documentava come
        // se fosse un contratto accettabile.
        for (size_t i = 0; i < pren.size(); i++) {
            if (ok[i]) {
                psx_cache_unpin(c, pren[i].region, pren[i].offset);
            }
        }
        s->fail("lettura di una slice fallita dentro un blocco");
    }
    return tutte;
}


// ===========================================================================
// emetti e continua: la lettura parte, il thread va avanti, si aspetta alla fine
// ===========================================================================
//
// Il blocco di n slice non bastava, e il motivo e' aritmetico. Per raccogliere
// piu' miss in un blocco bisogna consumare piu' slice per lotto; ma su 45 slice e
// 12 thread, lotti da 4 lasciano 12 lotti, cioe' zero margine per il
// bilanciamento dinamico, e il costo di quello supera il guadagno (misurato:
// attesa fra thread 2,26x, parete +8%). Blocco 2 e' il massimo sotto quel
// vincolo, e raccoglie solo 1,48 letture su 2 possibili.
//
// Le due cose si slegano notando *quando* i byte servono davvero: non al ritorno
// di ensure, ma alla barriera prima del calcolo. Quindi un thread puo' emettere
// la lettura e continuare a prendere slice una alla volta — distribuzione fine,
// come il blocco 1 — e aspettare tutte le sue letture insieme alla fine, con una
// profondita' di coda pari ai miss che ha incontrato, non a una costante.
namespace {

struct Sospesa {
    Prenotazione pr;
    // Argine2 research timing (filled only when ARGINE_RESEARCH_TRACE is set)
    uint64_t req         = 0;
    uint64_t issue_ns    = 0;
    uint64_t complete_ns = 0;
    uint32_t flags       = 0;
    bool     esito       = false;
#if defined(_WIN32)
    OVERLAPPED ov;
    size_t     i_evento = 0;
    int        emessa    = 0;  // 0 no, 1 in volo, 2 completata subito
#else
    bool letta = false;
#endif
};

struct StatoThread {
    // **deque, non vector.** ReadFile riceve il puntatore all'OVERLAPPED e il
    // kernel lo tiene per tutta la durata della lettura: se il contenitore
    // rialloca mentre l'I/O e' in volo, quel puntatore diventa pendente. Con un
    // vector la prima riallocazione rompeva tutte le letture gia' emesse, e dal
    // vivo si vedeva come "impossibile rendere residenti gli expert" con il budget
    // quasi vuoto — 21 MiB usati su 2048 — perche' le letture fallite venivano
    // disfatte e la residenza crollava.
    //
    // Il deque garantisce che i riferimenti agli elementi restino validi quando si
    // aggiunge in coda. Il static_assert sotto impedisce che qualcuno torni a un
    // vector senza accorgersene.
    std::deque<Sospesa> sospese;
#if defined(_WIN32)
    std::vector<HANDLE> eventi;
    Borrowed            preso{ -1, INVALID_HANDLE_VALUE };
#endif
};

static_assert(std::is_same<decltype(StatoThread::sospese), std::deque<Sospesa>>::value,
              "le sospese vogliono un contenitore con indirizzi stabili: il kernel "
              "tiene il puntatore all'OVERLAPPED finche' la lettura e' in volo");

StatoThread & mio_stato() {
    static thread_local StatoThread st;
    return st;
}

#if defined(_WIN32)
// Argine2: stamp the reads that have completed since the last look. Cheap
// (HasOverlappedIoCompleted reads one field of the OVERLAPPED), called while
// the thread keeps issuing, so that the completion time of a read finished
// before the wait is bounded tightly instead of being "sometime before".
// Every stamp taken this way is an UPPER BOUND and flagged as such.
void arx_poll(StatoThread & st) {
    uint64_t now = 0;
    for (auto & so : st.sospese) {
        if (so.emessa == 1 && so.complete_ns == 0 && HasOverlappedIoCompleted(&so.ov)) {
            if (!now) {
                now = arx_now_ns();
            }
            so.complete_ns = now;
            so.flags |= ARX_FLAG_COMPLETE_BOUND;
        }
    }
}

// Argine2: wait for the outstanding reads one completion at a time, so that
// each completion is timestamped when it happens (to the wake-up latency of
// this thread) instead of when the sequential loop below gets to it. Used only
// while tracing; the results are then collected by the unchanged loop.
void arx_wait_precise(StatoThread & st) {
    arx_poll(st);
    for (;;) {
        HANDLE    h[MAXIMUM_WAIT_OBJECTS];
        Sospesa * who[MAXIMUM_WAIT_OBJECTS];
        DWORD     k = 0;
        for (auto & so : st.sospese) {
            if (so.emessa == 1 && so.complete_ns == 0 && k < MAXIMUM_WAIT_OBJECTS) {
                h[k]   = st.eventi[so.i_evento];
                who[k] = &so;
                k++;
            }
        }
        if (k == 0) {
            return;
        }
        const DWORD    r   = WaitForMultipleObjects(k, h, FALSE, INFINITE);
        const uint64_t now = arx_now_ns();
        if (r >= WAIT_OBJECT_0 && r < WAIT_OBJECT_0 + k) {
            who[r - WAIT_OBJECT_0]->complete_ns = now;
            who[r - WAIT_OBJECT_0]->flags |= ARX_FLAG_COMPLETE_EXACT;
        } else {
            return;  // wait failed: GetOverlappedResult below reports the error
        }
        for (auto & so : st.sospese) {
            if (so.emessa == 1 && so.complete_ns == 0 && HasOverlappedIoCompleted(&so.ov)) {
                so.complete_ns = now;
                so.flags |= ARX_FLAG_COMPLETE_BOUND;
            }
        }
    }
}
#endif

}  // namespace

int psx_cache_avvia(struct psx_cache * c, struct psx_store * s, struct psx_region * region,
                    size_t offset, uint32_t layer, uint32_t expert, enum psx_kind kind) {
    if (!c || !s || !region) {
        return 0;
    }
    Prenotazione pr = prenota(c, s, region, offset, layer, expert, kind);
    if (pr.stato == Prenotazione::ERRORE) {
        return 0;
    }
    const bool arx = arx_enabled() != 0;
    if (pr.stato == Prenotazione::HIT) {
        if (arx) {
            arx_access(layer, expert, (uint32_t) kind, pr.atteso_altro ? 'w' : 'h',
                       psx_slice_read_bytes(s, layer, expert, kind), 0, pr.shard_id, pr.seq);
        }
        return 1;
    }

    StatoThread & st = mio_stato();
#if defined(_WIN32)
    if (arx) {
        arx_poll(st);
    }
#endif
    if (g_pretouch.load(std::memory_order_relaxed)) {
        memset(pr.dst, 0, pr.need);
    }
    st.sospese.push_back(Sospesa{});
    Sospesa & so = st.sospese.back();
    so.pr = pr;
    if (arx) {
        so.req = arx_next_request_id();
        arx_access(layer, expert, (uint32_t) kind, 'm', pr.utili, so.req, pr.shard_id, pr.seq);
    }

#if defined(_WIN32)
    if (st.preso.index < 0) {
        st.preso = acquire_handle(s);
        if (st.preso.index < 0) {
            // Nessun descrittore: si legge subito, in modo sincrono. Piu' lento,
            // ma non si perde la slice.
            const uint64_t t_iss = arx ? arx_now_ns() : 0;
            const bool ok = read_at(s, pr.file_off, pr.dst, pr.want);
            if (arx) {
                const uint64_t t_end = arx_now_ns();
                arx_io(so.req, layer, expert, (uint32_t) kind, pr.utili, t_iss, t_end, t_iss, t_end,
                       ok, ARX_FLAG_SYNCHRONOUS | ARX_FLAG_FALLBACK | ARX_FLAG_COMPLETE_EXACT);
            }
            completa(c, pr, ok);
            st.sospese.pop_back();
            return ok ? 1 : 0;
        }
    }
    const size_t i = st.sospese.size() - 1;
    while (st.eventi.size() <= i) {
        HANDLE ev = CreateEventA(nullptr, TRUE, FALSE, nullptr);
        if (!ev) {
            break;
        }
        st.eventi.push_back(ev);
    }
    if (i >= st.eventi.size()) {
        const uint64_t t_iss = arx ? arx_now_ns() : 0;
        const bool ok = read_at(s, pr.file_off, pr.dst, pr.want);
        if (arx) {
            const uint64_t t_end = arx_now_ns();
            arx_io(so.req, layer, expert, (uint32_t) kind, pr.utili, t_iss, t_end, t_iss, t_end, ok,
                   ARX_FLAG_SYNCHRONOUS | ARX_FLAG_FALLBACK | ARX_FLAG_COMPLETE_EXACT);
        }
        completa(c, pr, ok);
        st.sospese.pop_back();
        return ok ? 1 : 0;
    }
    so.i_evento = i;
    memset(&so.ov, 0, sizeof(OVERLAPPED));
    so.ov.Offset     = (DWORD) (pr.file_off & 0xFFFFFFFFull);
    so.ov.OffsetHigh = (DWORD) (pr.file_off >> 32);
    so.ov.hEvent     = st.eventi[i];
    ResetEvent(st.eventi[i]);
    DWORD got = 0;
    if (arx) {
        so.issue_ns = arx_now_ns();
    }
    if (ReadFile(st.preso.h, pr.dst, (DWORD) pr.want, &got, &so.ov)) {
        so.emessa = 2;
        if (arx) {
            // completed inside ReadFile: observed the moment the call returned
            so.complete_ns = arx_now_ns();
            so.flags |= ARX_FLAG_COMPLETE_EXACT;
        }
    } else if (GetLastError() == ERROR_IO_PENDING) {
        so.emessa = 1;
    } else {
        so.emessa = 0;
    }
#else
    // POSIX: niente asincrono portabile, si legge subito. La contabilita' resta
    // identica, manca solo la sovrapposizione.
    if (arx) {
        so.issue_ns = arx_now_ns();
    }
    so.letta = read_at(s, pr.file_off, pr.dst, pr.want);
    if (arx) {
        so.complete_ns = arx_now_ns();
        so.flags |= ARX_FLAG_SYNCHRONOUS | ARX_FLAG_COMPLETE_EXACT;
    }
#endif
    return 2;
}

int psx_cache_attendi(struct psx_cache * c, struct psx_store * s) {
    if (!c || !s) {
        return 1;
    }
    StatoThread & st = mio_stato();
    if (st.sospese.empty()) {
        return 1;
    }
    using orologio = std::chrono::steady_clock;
    const auto t0 = orologio::now();
    const bool     arx        = arx_enabled() != 0;
    const uint64_t wait_start = arx ? arx_now_ns() : 0;
#if defined(_WIN32)
    if (arx) {
        arx_wait_precise(st);
    }
#endif

    int tutte = 1;
    for (auto & so : st.sospese) {
        bool ok = false;
#if defined(_WIN32)
        if (so.emessa) {
            DWORD got = 0;
            ok = GetOverlappedResult(st.preso.h, &so.ov, &got, TRUE) && got >= so.pr.want;
        }
#else
        ok = so.letta;
#endif
        if (ok && guasto_iniettato()) {
            ok = false;  // guasto iniettato per i test
        }
        completa(c, so.pr, ok);
        so.esito = ok;
        if (!ok) {
            tutte = 0;
        }
    }
    if (arx) {
        // wait_start/wait_end delimit the interval in which this thread was
        // blocked on its own reads (the same for the whole group). The part of
        // [issue, complete] before wait_start was hidden behind other work of
        // this thread; the rest was not.
        const uint64_t wait_end = arx_now_ns();
        for (auto & so : st.sospese) {
            uint64_t done  = so.complete_ns;
            uint32_t flags = so.flags;
            if (!done) {
                done = wait_end;  // never observed earlier: only bounded by the end of the wait
                flags |= ARX_FLAG_COMPLETE_BOUND;
            }
            arx_io(so.req, so.pr.layer, so.pr.expert, (uint32_t) so.pr.kind, so.pr.utili,
                   so.issue_ns, done, wait_start, wait_end, so.esito, flags);
        }
    }
    c->ns_read.fetch_add(
        (uint64_t) std::chrono::duration_cast<std::chrono::nanoseconds>(
            orologio::now() - t0).count(),
        std::memory_order_relaxed);
    c->blocchi.fetch_add(1, std::memory_order_relaxed);
    c->letture.fetch_add(st.sospese.size(), std::memory_order_relaxed);

#if defined(_WIN32)
    if (st.preso.index >= 0) {
        release_handle(s, st.preso.index);
        st.preso.index = -1;
    }
#endif
    if (!tutte) {
        s->fail("una lettura sospesa non e' arrivata");
    }
    st.sospese.clear();
    return tutte;
}

// --- iniezioni per i test ---------------------------------------------------

void psx_debug_set_read_delay_us(unsigned us) {
    g_read_delay_us.store(us);
}

void psx_debug_fail_next_reads(unsigned n) {
    g_fail_reads.store(n);
}

void psx_debug_set_pretouch(int on) {
    g_pretouch.store(on ? 1 : 0);
}
