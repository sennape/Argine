# Patch per llama.cpp

Patch che Argine applica a una copia di llama.cpp per abilitare funzioni che
il progetto ufficiale non espone. Non servono all'utente finale: Argine
funziona con i binari ufficiali, e queste aggiungono capacita' di misura e
ottimizzazione.

## 0001-argine-route-log.patch

Registra le scelte del router MoE su file, attivandosi con la variabile
d'ambiente `ARGINE_ROUTE_LOG`. Senza la variabile il costo e' una sola
lettura di puntatore per operazione.

Formato prodotto, una riga per chiamata a `MUL_MAT_ID`:

    <nome_tensore> <n_token> <n_expert_usati> <id> <id> ...

Serve a `argine reorder`, che dalla traccia ricava quali expert si attivano
insieme e riscrive il GGUF in modo che siano contigui su disco.

### Applicazione

```bash
git clone --depth 1 https://github.com/ggml-org/llama.cpp
cd llama.cpp
git apply ../patches/0001-argine-route-log.patch
cmake -B build -DCMAKE_BUILD_TYPE=Release -G Ninja
cmake --build build --target llama-server -j
```

Poi si indica a Argine dove trovare i binari:

```bash
export ARGINE_LLAMA_DIR=/percorso/llama.cpp/build/bin
```

### Verifica

Il riordino e' una rietichettatura esatta: a parita' di prompt e temperatura 0,
il modello riordinato produce output identico byte per byte all'originale.
Verificato su un qwen3moe di prova (8 expert, 2 attivi, 2 layer).

## argine-store.{h,cpp} — lettore dell'expert store e residenza

Modulo autonomo: non include niente di llama.cpp o di ggml, cosi' si compila e
si collauda da solo. Il buffer type che lo usera' e' un altro file, ancora da
scrivere.

Fa tre cose:

1. **legge l'expert store** prodotto da `argine store build`, con letture
   posizionate esplicite e, se richiesto, `FILE_FLAG_NO_BUFFERING`;
2. **gestisce la residenza**: riserva lo spazio di indirizzamento di un tensore
   per intero e impegna la memoria fisica una slice per volta, cosi' il
   puntatore che ggml riceve resta valido e contiguo mentre la RAM occupata e'
   solo quella delle slice in cache;
3. **applica un budget rigido** con una LRU, i cui slot in uso sono bloccati.

### Perche' esiste

Misurato su i5-1340P, 15.72 GiB, Qwen3-30B-A3B Q4_K_M, sullo stesso disco e con
lo stesso pattern di accesso degli expert:

| percorso | MB/s dal disco |
|---|---|
| memoria mappata, come fa llama.cpp oggi | 562 in generazione |
| letture esplicite, offset allineati, senza cache dell'OS | 4264 |

La differenza non e' il disco: e' il percorso di page fault. A un thread, sulla
stessa slice da 896 KiB e con cache fredda in entrambi i casi, la memoria
mappata rende 310 MB/s e una `ReadFile` 1324 — 4.3x, con la latenza che passa da
2.96 ms a 0.69. Windows non fault-a 896 KiB in una volta: ne prende un pezzo per
volta e paga un giro sull'NVMe ogni volta.

### Collaudo

```bash
g++ -O2 -std=c++17 -Wall -Wextra argine-store.cpp argine-store-test.cpp -o psxtest
./psxtest <store.psxs> <atteso.bin> <n_layers> <n_experts>

g++ -O2 -std=c++17 -I. argine-store.cpp argine-store-stress.cpp -o psxstress
NT=16 DLY=50 SLOTS=3 ./psxstress <store.psxs> x <n_layers> <n_experts>
```

`atteso.bin` sono le slice lette **dal GGUF** e concatenate nell'ordine
(layer, expert, kind): il confronto e' contro la sorgente vera, non contro lo
store stesso. Lo produce il test Python `TestExpertStore`.

Entrambi puliti sotto ThreadSanitizer e AddressSanitizer/UBSan.

### Quattro difetti che il collaudo ha trovato, e che vale la pena ricordare

Sono tutti e quattro invisibili a occhio e tutti e quattro fatali dal vivo.

**1. `ensure` tornava prima che i byte fossero arrivati.** La slice veniva
annunciata nell'indice e letta dopo; un altro thread trovava un hit e calcolava
su memoria non inizializzata. Con `MUL_MAT_ID`, che tutti i thread eseguono
sullo stesso tensore, succedeva a ogni miss. Non si vedeva perche' leggere
8 KiB dalla page cache dura un microsecondo e avviare un thread cinquanta:
la finestra c'era ma non ci si inciampava mai. Da qui
`psx_debug_set_read_delay_us`, che la allarga a comando: con 300 us il test
contava 430 letture sporche su 480. Corretto con tre stati (assente, in
caricamento, pronta) e attesa su condition variable.

**2. `ensure` non teneva la slice.** Tornava 1 e un altro thread poteva
sfrattarla un istante dopo: l'accesso finiva su memoria decommitata, cioe' un
segfault. Trovato dal test da stress alla prima esecuzione. Corretto con il
blocco (`psx_cache_unpin`), e da qui il vincolo che il budget non puo' essere
piu' piccolo del working set di una singola operazione
(`psx_cache_min_budget`). Nota per il dimensionamento: durante il prompt un
ubatch tocca molti piu' expert che durante la generazione, nel caso peggiore
tutti, quindi il minimo si calcola sul caso prompt.

**3. Uno stato letto prima di un'attesa e usato dopo.** Il ciclo di sfratto
aspettava spazio con `wait_for`, che rilascia il mutex, ma la decisione "questa
chiave e' assente" era stata presa prima di aspettare. Al risveglio si inseriva
un secondo slot per la stessa chiave: `index[key]` sovrascriveva il primo, il
suo nodo restava orfano nella lista, gli unpin finivano sul nodo sbagliato e si
arrivava a sfrattare memoria in uso. Il test da stress lo mostrava come
segfault intermittente (2 volte su 3 con 16 thread) e il diario degli eventi
come due `load` consecutivi sullo stesso offset. Corretto ricominciando dalla
ricerca dopo ogni attesa. Effetto collaterale della correzione: gli hit sono
passati da 232 a 2232 su 4800, perche' ogni caricamento duplicato contava come
miss e raddoppiava le letture.

**4. Un descrittore usato fuori dal lock.** `read_with` dereferenziava
`s->handles[idx]` senza il mutex, mentre un altro thread apriva un descrittore e
faceva riallocare il vettore. Segnalato da ThreadSanitizer come corsa in
`_M_realloc_insert`; dal vivo sarebbe stato un use-after-free raro. Corretto
copiando il valore del descrittore sotto il lock.

---

## Argine2 — `0003-argine-research-trace.patch` and `argine-research.{h,cpp}` (English)

Research instrumentation for Phase 1 of Argine2 (observation only). The patch itself only adds the two source files to `ggml/src/ggml-cpu/CMakeLists.txt`; the hooks live in `argine-store.cpp` and `argine-experts.cpp`, which are copied into llama.cpp as before.

Activated by `ARGINE_RESEARCH_TRACE=<path>` (`%p` = process id). Writes a tab-separated raw log (format in the header of `argine-research.h`): router decisions per forward pass and layer, every slice access with outcome (hit / miss / waited), every read with a unique request id and issue / completion / wait timestamps (completion flagged exact or upper bound), evictions with resident time, the residency phase of every MoE node, and cache snapshots including a hard-budget violation counter (`violazioni`, must be 0).

**Format v2** (instrumentation id `argine2-research-v2`, `H` record version 2, 2026-10-03, EXP-021). It adds what offline replay needs to reproduce the store decision by decision:
- `A` (access) records carry the shard id and a per-shard sequence number. The number is assigned under the shard mutex, so the per-shard order is exactly the order of the store's decisions.
- `E` (eviction) records carry shard, sequence and the number of pinned or loading slots skipped before the victim.
- A new `U` (undo) record marks a reservation removed after a failed read.

The parser (`argine/research/rawlog.py`) reads v1 and v2. The v1 binaries in `llama-research/` remain the provenance of the EXP-007 runs; the v2 binaries are in `llama-research-v2/`. Cache behaviour is identical: only the records differ.

`argine-store-fidelity-test.cpp` drives the real store deterministically: one thread, gate/up/down nodes as `argine-experts.cpp` issues them, every 25th step a prompt step. It writes a v2 trace that `tests/test_fidelity.py` replays with `argine/research/storereplay.py` and compares decision by decision:

```bash
g++ -O1 -std=c++17 argine-store.cpp argine-research.cpp argine-store-fidelity-test.cpp -o fidtest -lpthread
./fidtest <store.psxs> <n_layers> <n_experts> <trace> <budget> <shards> <steps> <seed> [pools]
```

`pools = 2` runs prompt steps on one persistent thread and decode steps on another, strictly in turn. This reproduces llama.cpp with `--threads ≠ --threads-batch`, where each pool keeps its pins while the other computes (EXP-021, Windows v2 follow-up).

**Trace tail at termination.** Each thread writes its buffer into the file at the end of every node, and the file is `fflush`ed at most every 250 ms. Records written after the last flush are lost when llama-server is terminated. The flush-sentinel request exists so that this tail is never workload. `argine research fidelity` checks it: missing sequence numbers are classified, and node completeness is checked against the router records.

Order of application on llama.cpp `254b177307b14d055bbe4c050c9c441a1e06d8a4`:

```bash
git apply ../patches/0002-argine-expert-store.patch
cp ../patches/argine-{store,experts,research}.{h,cpp} ggml/src/ggml-cpu/
git apply ../patches/0001-argine-route-log.patch
git apply ../patches/0003-argine-research-trace.patch
```

Newer llama.cpp (from `631109b`, 2026-10-02) adds `alloc_buffer_n` to the buffer-type interface; `argine-experts.cpp` must then initialise that member (not done yet: the commit is pinned instead).

### Test

```bash
g++ -O1 -g -std=c++17 -fsanitize=thread argine-store.cpp argine-research.cpp argine-research-test.cpp -o arxtest -lpthread
./arxtest <store.psxs> <n_layers> <n_experts> <trace-file>
```

Checks: identical cache statistics with and without the trace (observation only); per-shard sequence numbers gapless and covering every shard (v2); exactly one read record per miss with the same request id; issue ≤ completion and wait start ≤ wait end; every completion flagged exact xor upper bound; every eviction logged; no budget violation with 6 threads. Clean under ThreadSanitizer and AddressSanitizer/UBSan; the existing store test (79 checks) and stress test also pass with tracing on. `tests/test_research.py` compiles and runs it automatically when `g++` is available.

### What has and has not been executed

- Linux, synchronous POSIX read path: unit tests + end-to-end llama-server run (EXP-019).
- Windows overlapped-I/O path (precise completion stamps with `WaitForMultipleObjects`, polling with `HasOverlappedIoCompleted`): compiled with MinGW-w64, **not yet executed** *(historical wording, 2026-10-03)*. [2026-10-12] It has since been executed on Windows: the 2026-10-03 smoke run (66.6% exact completions) and the EXP-019 validation of 2026-10-12 (63.4% exact, the rest upper bounds).

---

## `0005-argine-prefetch-race-fix.patch` (R2-12 Phase F)

**What it fixes.** `0001-argine-route-log.patch` bundles two unrelated things under one name: the documented routing logger above, and an undocumented asynchronous prefetch subsystem (`argine_prefetch`, `argine_issue_prefetch`, `argine_worker_main`, gated by `ARGINE_PREFETCH`, default off) that was never mentioned in this file. Under `ARGINE_PREFETCH>0`, that subsystem spawns raw `CreateThread` worker threads, completely outside the ggml threadpool and its barrier, to warm pages for experts the router is predicted to need a few layers ahead. When `PrefetchVirtualMemory` cannot be resolved (`GetProcAddress` returns null), the pre-fix code fell back to touching one byte per page directly: `volatile const char * p = ...; (void) p[off];`.

That fallback has zero coordination with `psx_region`/the retention policy. A page it touches can be one the policy has already evicted (`psx_region_decommit` → `VirtualFree(MEM_DECOMMIT)` → `PAGE_NOACCESS`) to make room for the current node's experts, from a thread that never waits on `ggml_barrier`. Dereferencing `PAGE_NOACCESS` memory is an unhandled Win32 access violation: silent (no `GGML_ABORT`, no assert, nothing our own code ever prints), on a thread outside the compute barrier — the same shape as the step-320 host crash (empty stderr, no admission message, process gone). `patches/argine-prefetch-race-test.cpp` reproduces the mechanism on Linux (`mprotect(PROT_NONE)` standing in for `VirtualFree(MEM_DECOMMIT)`): an unsynchronized raw-touch thread racing a commit/decommit cycle on the same pages segfaults on the first of five one-shot attempts, every time.

**The fix.** `argine_serve()`'s fallback now does nothing instead of guessing at memory safety: it returns without touching the pages. `PrefetchVirtualMemory` is present on every target this project supports (`_WIN32_WINNT`/`WINVER` = `0x0A00`, i.e. Windows 10+); a null `fn` here means something unexpected (an EDR hooking `kernel32.dll`, say), not an older OS that needs hand-rolled prefetching. Losing a prefetch hint costs a few ms of extra I/O in the worst case; the raw touch it replaced could cost the process.

**Status at the time this was found:** no documented R2-12 host run, launch script, or provenance file in this repository sets `ARGINE_PREFETCH`, and it defaults to 0 (disabled) when unset — so this is a real, demonstrated, now-fixed hazard on the exact "other threads consuming expert tensors" boundary, but it is **not confirmed** to be the trigger of the specific step-320/layer-10 host crash under investigation unless that host run's actual launch command set `ARGINE_PREFETCH`. It is fixed regardless, since leaving it in place is unsafe independent of whether it explains this particular crash.

### Collaudo

```bash
g++ -std=c++17 -O1 -g -pthread -fsanitize=address,undefined \
    -o argine-prefetch-race-test argine-prefetch-race-test.cpp
ASAN_OPTIONS=handle_segv=0:handle_sigbus=0:handle_abort=0 ./argine-prefetch-race-test
```

Part 1 forks a child that runs the pre-fix raw-touch logic racing a commit/decommit cycle and expects `SIGSEGV`/`SIGBUS` (observed on 5/5 and repeated full-suite runs, 100% reproducible). Part 2 runs the same race with the fixed logic for 20000 cycles in-process and requires zero crashes.

---

## Partial-kind admission under a retention policy (R2-12 Phase F, root cause)

**What it fixes.** Real Windows host crash on v5: Application Error 1000, exception `0x40000015` (`STATUS_FATAL_APP_EXIT`, not an access violation), fault offset `0x6df7cc`, identical on both policies. Mapping that RVA against v5's own PE image lands inside `ggml_abort()`, at the instruction right after `call abort` — the standard signature of this UCRT's own `abort()` (via `GGML_ABORT`), with the `fprintf(stderr, ...)` message written to the stream buffer but never flushed before the fail-fast exit (`abort()` does not flush stdio; only `exit()` does). That explains "no useful abort text" despite a real message having been produced.

`dove[]` (argine-experts.cpp) includes a kind other than the current node's own only once `c.posizioni` has registered it — true for every kind eventually, but not necessarily on a layer's *first* visit. `servi_evento_con_politica` (argine-store.cpp) computes `bytes_of_layer` from only the kinds present in that one call — a pre-existing, documented accommodation for tensors that permanently live on a different buffer path. `RetentionPolicy::access()` (frozen, faithful to the Python `AgedFrequency` reference) tracks residency per `Obj{layer,expert}`, and on a hit updates only `score`/`last_ref` — never `ObjState.bytes`. Combined: an object's very first admission under a transiently-incomplete `dove[]` permanently under-registers its weight; a later event needing a kind that was missing at first admission is a logical hit with no physical index entry for that kind. Fase 1 already detected this exact inconsistency and failed closed with `"non e' nell'indice ma la politica non lo elenca come mancante"` — correct to catch it, fatal in response.

**The fix.** That branch no longer calls `s->fail()`+abort. It services the kind through the identical commit+read+index path already used for a genuine miss — a physical self-heal, with zero lines of `argine-retention-policy.{h,cpp}` touched (no score, residency, eviction, predictor, or budget field read or written). A new counter, `psx_retention_stats.policy_hit_sans_index_healed`, and a one-time `stderr` note make this visible without being fatal.

**Proof.** `patches/argine-partial-kind-admission-test.cpp` drives the real compiled `argine-store.cpp`/`argine-retention-policy.cpp` against the real 30B store, no llama.cpp/ggml involved: event 1 admits 5 experts with only their `gate` kind; event 2 requests the same experts' `gate+up`. Built with `-DARGINE_PHASE_F_PRE_FIX_DEMO` against commit `6d07f4c` (pre-fix), event 2 fails with the exact host abort message. Built against this commit (post-fix), event 2 succeeds, `policy_hit_sans_index_healed` reads exactly 5, and a third repeat event confirms convergence (no further healing once both kinds are indexed). `patches/argine-retention-integration-test.cpp` section 12 (which used to assert the old fatal failure) now asserts the healing instead.

**Harness.** `tools/r2-12-host-conformance.ps1` replaces an earlier rejected draft that waited on `llama-server.exe` as a one-shot batch process and called the wrong comparator. It runs the server as a background HTTP service, polls `/health`, drives one real `/completion` request per policy, and validates against `tools/r2_12_real_trace_conformance.py` (the real per-trace-vs-frozen-Python comparator) on the decision-line-only subset of the trace.

Realized additively as `llama-research-v7` (never overwrites v5 or v6).
