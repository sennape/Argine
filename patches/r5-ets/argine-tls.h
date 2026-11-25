// Research 5 (ETS) -- per-thread objects whose destructor runs while their storage is still alive.
//
// Why this exists (R5 Phase 1a host incident, STATUS_HEAP_CORRUPTION 0xC0000374 in argine-ets-test.exe):
// with the mingw-w64 GCC 10 toolchain used for every Argine Windows build, `thread_local` lives in libgcc's
// emulated TLS (emutls) and the destructors of `thread_local` objects are registered through libstdc++'s generic
// __cxa_thread_atexit fallback. Both clean up with a winpthreads key destructor at thread exit, and winpthreads runs
// key destructors in ascending key order. The emutls key is created by the first TLS access, i.e. always before the
// libstdc++ key (created when the first thread_local with a destructor is constructed), so emutls_destroy() frees
// every thread_local object of the exiting thread FIRST and the C++ destructors then run on freed memory
// (std::deque / std::vector members free buffers through pointers read from freed blocks). glibc runs thread_local
// destructors before releasing TLS, which is why Linux, ASan and TSan never saw it.
//
// argine_thread_object<T, Tag>() returns this thread's T for the call site identified by Tag (one object per
// (T, Tag) and thread, exactly like one `static thread_local T` per call site). On mingw (or with ARGINE_TLS_FORCE_KEYED, used by the POSIX
// tests to exercise this path) the T lives on the heap behind a dedicated pthread key whose destructor deletes it:
// it never depends on emutls storage, so its destructor runs on live memory whatever order the keys are destroyed in.
// Elsewhere (glibc, MSVC) it is the plain `static thread_local T`. The object, its construction (first use on the
// thread) and its destruction (thread exit) are the same on both paths; only the storage changes.
#pragma once

#include <atomic>
#include <cstdint>
#include <cstdlib>

#if defined(__MINGW32__) || defined(ARGINE_TLS_FORCE_KEYED)
#    define ARGINE_TLS_KEYED 1
#    include <pthread.h>
#else
#    define ARGINE_TLS_KEYED 0
#endif

// Counters for the conformance test: per-thread objects created / destroyed (both paths).
inline std::atomic<uint64_t> g_argine_tls_created{ 0 };
inline std::atomic<uint64_t> g_argine_tls_destroyed{ 0 };

#if ARGINE_TLS_KEYED
#    include <mutex>
// Process exit: like libstdc++'s fallback (std::atexit(run), registered when the first per-thread object is
// created), the objects of the thread that runs exit() -- the main thread -- are destroyed by an atexit handler;
// other threads' objects are destroyed by their key destructor at thread exit.
struct argine_tls_registry {
    struct Entry { pthread_key_t key; void (*destroy)(void *); };
    static constexpr int kMax = 16;
    Entry      e[kMax];
    int        n = 0;
    std::mutex m;
};
inline argine_tls_registry & argine_tls_reg() {
    static argine_tls_registry r;
    return r;
}
inline void argine_tls_at_exit() {
    argine_tls_registry & r = argine_tls_reg();
    for (int i = r.n - 1; i >= 0; i--) {   // reverse creation order, as the libstdc++ cleanup list
        void * p = pthread_getspecific(r.e[i].key);
        if (p) {
            pthread_setspecific(r.e[i].key, nullptr);
            r.e[i].destroy(p);
        }
    }
}
template <typename T, typename Tag> struct argine_tls_key {
    pthread_key_t key;
    argine_tls_key() {
        if (pthread_key_create(&key, &argine_tls_key::destroy) != 0) {
            abort();  // no per-thread storage: refuse to run rather than fall back silently
        }
        argine_tls_registry & r = argine_tls_reg();
        std::lock_guard<std::mutex> lk(r.m);
        if (r.n == argine_tls_registry::kMax) {
            abort();
        }
        if (r.n == 0) {
            std::atexit(argine_tls_at_exit);
        }
        r.e[r.n++] = { key, &argine_tls_key::destroy };
    }
    static void destroy(void * p) {
        delete static_cast<T *>(p);
        g_argine_tls_destroyed.fetch_add(1, std::memory_order_relaxed);
    }
};
#endif

template <typename T, typename Tag> inline T & argine_thread_object() {
#if ARGINE_TLS_KEYED
    static argine_tls_key<T, Tag> k;  // one key per call site (thread-safe static initialisation)
    T * p = static_cast<T *>(pthread_getspecific(k.key));
    if (!p) {
        p = new T();
        if (pthread_setspecific(k.key, p) != 0) {
            abort();
        }
        g_argine_tls_created.fetch_add(1, std::memory_order_relaxed);
    }
    return *p;
#else
    // native TLS (glibc, MSVC): destructors run before the thread's TLS is released
    struct Holder {
        T obj;
        Holder() { g_argine_tls_created.fetch_add(1, std::memory_order_relaxed); }
        ~Holder() { g_argine_tls_destroyed.fetch_add(1, std::memory_order_relaxed); }
    };
    static thread_local Holder h;
    return h.obj;
#endif
}
