# CHEAP rule (frozen on TRAIN)

Evict, among the candidates (residents not requested by the current event; all residents if none), the one with the HIGHEST leaf rank.
Ties: larger `events_since_last_ref` (older), then lower layer, then lower expert.

```
if mean_reuse_interval <= 13:
  if mean_reuse_interval <= 3:
    if layer_cycle_offset <= 20:
      -> leaf rank 0  (mean log2(1+next-use distance) = 5.545, rows 501678)
    else:
      -> leaf rank 1  (mean log2(1+next-use distance) = 6.753, rows 822162)
  else:
    if pending_misses <= 8:
      -> leaf rank 2  (mean log2(1+next-use distance) = 7.474, rows 1863612)
    else:
      -> leaf rank 5  (mean log2(1+next-use distance) = 11.178, rows 51438)
else:
  if mean_reuse_interval <= 33:
    if pending_misses <= 8:
      -> leaf rank 3  (mean log2(1+next-use distance) = 8.880, rows 330698)
    else:
      -> leaf rank 7  (mean log2(1+next-use distance) = 11.933, rows 16437)
  else:
    if reload_count <= 0:
      -> leaf rank 4  (mean log2(1+next-use distance) = 9.861, rows 128509)
    else:
      -> leaf rank 6  (mean log2(1+next-use distance) = 11.900, rows 151460)
```

Features used: reload_count, mean_reuse_interval, pending_misses, layer_cycle_offset.  Tree hash: `8603908b188c3346c7ed86e1d6914f378c869e017a305fccf9b1adafc734fcf2`.
