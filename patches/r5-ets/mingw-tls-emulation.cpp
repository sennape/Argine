// Research 5 (ETS) -- emulation of the mingw-w64 GCC 10 thread_local teardown on Linux, for tests only.
// Reproduces, under AddressSanitizer, the Windows host incident of the first G1 attempt (argine-ets-test.exe:
// STATUS_HEAP_CORRUPTION) and proves the argine-tls.h fix on the keyed path:
//
//   clang++ -O1 -g -std=c++17 -pthread -femulated-tls -fsanitize=address -DETS_TEST_NO_DEFERRED -DARGINE_TLS_FORCE_KEYED \
//     -I. argine-store.cpp argine-research.cpp ../argine-retention-policy.cpp argine-ets-test.cpp mingw-tls-emulation.cpp
//
// With the pre-fix sources (plain thread_local) the same build stops with heap-use-after-free in ~StatoThread, called
// from run() below after libgcc's emutls_destroy freed the object (MODEL_FREE_CONFORMANCE.json, mingw_tls_emulation).
// Linked together with -femulated-tls: thread_local objects then live in libgcc's emutls (one pthread key whose
// destructor frees them), and this file replaces glibc's __cxa_thread_atexit with the key-based fallback that
// libstdc++ 10.3 uses on mingw (libsupc++/atexit_thread.cc, the branch without __cxa_thread_atexit_impl).
// glibc runs pthread key destructors in ascending key order, as winpthreads' _pthread_cleanup_dest does.
#include <pthread.h>
#include <cstdlib>
#include <new>
namespace {
struct elt { void (*destructor)(void *); void *object; elt *next; };
pthread_key_t key;
void run(void *p) {
    elt *e = static_cast<elt*>(p);
    while (e) { elt *old_e = e; e->destructor(e->object); e = e->next; delete old_e; }
}
void run_main() { void *e = pthread_getspecific(key); pthread_setspecific(key, nullptr); run(e); }
void key_init() { pthread_key_create(&key, run); std::atexit(run_main); }
pthread_once_t once = PTHREAD_ONCE_INIT;
}
extern "C" int __cxa_thread_atexit(void (*dtor)(void *), void *obj, void *) noexcept {
    pthread_once(&once, key_init);
    elt *first = static_cast<elt*>(pthread_getspecific(key));
    elt *n = new (std::nothrow) elt;
    if (!n) return -1;
    n->destructor = dtor; n->object = obj; n->next = first;
    pthread_setspecific(key, n);
    return 0;
}
