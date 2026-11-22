// R2-12 Phase F: regressione che dimostra il pericolo del tocco grezzo
// nella vecchia argine_serve() (fallback senza PrefetchVirtualMemory) e
// verifica che la versione corretta (nessun tocco, solo "return") non lo
// riproduca mai.
//
// Non possiamo compilare/eseguire codice Win32 qui (siamo su Linux): il
// meccanismo esatto (VirtualFree(MEM_DECOMMIT) -> PAGE_NOACCESS, poi un
// dereferenziamento diretto da un thread fuori dal threadpool ggml e dalla
// sua barriera) si riproduce 1:1 con mprotect(PROT_NONE)/mprotect(PROT_READ)
// su una mappatura anonima: stessa cosa, pagine commesse o non accessibili,
// un thread "worker" indipendente che le tocca senza nessuna coordinazione.
//
// Il thread "worker non sicuro" gira in un processo figlio (fork): un
// SIGSEGV nel figlio e' la prova del pericolo senza far cadere la suite.
// Il thread "worker corretto" (la funzione dopo la fix) gira in processo,
// perche' non deve MAI toccare memoria: migliaia di cicli a zero crash sono
// la prova positiva.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <atomic>
#include <thread>
#include <chrono>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>

static const size_t PAGE = 4096;
static const size_t N_PAGES = 8;
static const int ITERS = 20000;

// Tocco grezzo: esattamente la vecchia riga di argine_serve() nel fallback
// senza PrefetchVirtualMemory -- un byte per pagina, senza alcuna verifica
// che la pagina sia committata.
static void old_unsafe_touch(volatile char * base, size_t size) {
    for (size_t off = 0; off < size; off += PAGE) {
        (void) base[off];
    }
}

// Versione corretta: nessun tocco quando non c'e' un'API sicura equivalente
// a PrefetchVirtualMemory -- esattamente il nuovo "return" in ggml-cpu.c.
static void new_safe_touch(volatile char *, size_t) {
    return;
}

static int run_race(bool unsafe, int iters) {
    void * mem = mmap(nullptr, N_PAGES * PAGE, PROT_NONE,
                       MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (mem == MAP_FAILED) {
        perror("mmap");
        return 2;
    }
    std::atomic<bool> stop{false};

    // "Committer": simula servi_evento_con_politica che rende residente e
    // poi, per un evento successivo, decommitta (psx_region_decommit) per
    // fare posto -- lo stesso ciclo ammissione/sfratto del nodo reale, ma
    // isolato dal resto della politica per tenere la regressione piccola.
    std::thread committer([&]() {
        for (int i = 0; i < iters && !stop.load(); i++) {
            mprotect(mem, N_PAGES * PAGE, PROT_READ | PROT_WRITE);
            memset(mem, 0x42, N_PAGES * PAGE);
            mprotect(mem, N_PAGES * PAGE, PROT_NONE);
        }
        stop.store(true);
    });

    // "Prefetch worker": thread nato con CreateThread, fuori dal threadpool
    // ggml e dalla sua barriera -- esattamente argine_worker_main. Non
    // sincronizzato in nessun modo con il committer sopra.
    std::thread worker([&]() {
        while (!stop.load()) {
            if (unsafe) {
                old_unsafe_touch((volatile char *) mem, N_PAGES * PAGE);
            } else {
                new_safe_touch((volatile char *) mem, N_PAGES * PAGE);
            }
        }
    });

    committer.join();
    worker.join();
    munmap(mem, N_PAGES * PAGE);
    return 0;
}

int main() {
    int fallimenti = 0;

    // --- Parte 1: dimostra il pericolo (comportamento PRE-fix) ---------
    // Il worker non sicuro gira in un figlio: se il SIGSEGV atteso arriva,
    // e' la prova che il tocco grezzo e' pericoloso. Qualche ripetizione
    // per non dipendere da una singola finestra di timing fortunata.
    bool visto_segv = false;
    for (int tentativo = 0; tentativo < 5 && !visto_segv; tentativo++) {
        pid_t pid = fork();
        if (pid == 0) {
            run_race(/*unsafe=*/true, ITERS);
            _exit(0);
        }
        int status = 0;
        waitpid(pid, &status, 0);
        if (WIFSIGNALED(status) && (WTERMSIG(status) == SIGSEGV || WTERMSIG(status) == SIGBUS)) {
            visto_segv = true;
        }
    }
    if (visto_segv) {
        printf("[OK] pre-fix: il tocco grezzo senza coordinazione produce un accesso "
               "a memoria PROT_NONE (SIGSEGV/SIGBUS) -- pericolo dimostrato\n");
    } else {
        printf("[NO] pre-fix: nessun SIGSEGV osservato in 5 tentativi -- la finestra di "
               "corsa non e' stata colta (non prova che il codice sia sicuro: la mancata "
               "osservazione di una race non e' la sua assenza)\n");
        fallimenti++;
    }

    // --- Parte 2: la versione corretta non deve MAI toccare memoria ----
    // Stesso ciclo ammissione/sfratto, stessa mancanza di sincronizzazione
    // fra i due thread: la sola differenza e' che il worker ora esegue
    // esattamente il nuovo corpo di argine_serve() (return immediato).
    // Nessun fork qui: deve semplicemente non esplodere.
    run_race(/*unsafe=*/false, ITERS);
    printf("[OK] post-fix: %d cicli ammissione/sfratto in corsa con il worker corretto, "
           "0 crash (il worker non tocca mai la memoria)\n", ITERS);

    printf(fallimenti == 0 ? "RISULTATO OK: 0 fallimenti\n"
                            : "RISULTATO FALLITO: %d fallimenti\n", fallimenti);
    return fallimenti == 0 ? 0 : 1;
}
