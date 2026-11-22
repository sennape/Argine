/* RICH ensemble inference: sequential double-precision accumulation, identical in arithmetic to the numpy reference path.
   Build: cc -O2 -ffp-contract=off -fno-fast-math -shared -fPIC -o librich.so _rich_infer.c -lm */
#include <math.h>
#define NODES 127
void rich_predict(int n, const double *X, int T, const int *feat, const double *thr, const int *left, const int *right,
                  const double *val, double init, double lr, long long *out) {
    for (int i = 0; i < n; i++) {
        double p = init;
        for (int t = 0; t < T; t++) {
            const int *f = feat + (long)t * NODES; const double *h = thr + (long)t * NODES;
            const int *l = left + (long)t * NODES; const int *r = right + (long)t * NODES;
            int node = 0;
            while (f[node] >= 0) node = (X[(long)i * 16 + f[node]] <= h[node]) ? l[node] : r[node];
            double q = lr * val[(long)t * NODES + node];
            p = p + q;
        }
        out[i] = llrint(p * 65536.0);
    }
}
