# R3-02H — R3-02G multi-session execution plan

R3-02 stays **MECHANISM_INSUFFICIENT**. R3-02G stays frozen; this phase adds a
scientifically neutral sessionization: 10 sessions x 60 pairs, with a single
immutable global order of 1,200 counted launches.

- Global schedule digest `ba7eb1b7e94f89ed1d5d02c4a9742f8307bc050860cc08fdd23aeae0832b5e40`
- 600 pairs, 1,200 counted launches, 300 balanced blocks (ABBA / BAAB alternating)
- 2 warmups total, session 1 only (no extra session warmups; adding them would
  change the frozen acquisition conditions)
- Session N = pairs 60(N-1)+1 .. 60N, 30 complete blocks, 120 counted launches
- One retry per launch, never counted; global N fixed at 600
- No interim analysis: the monitor shows only operational counts, RAM, disk
- Analysis unlocks only at 600/600 pairs and 1,200/1,200 admissible launches

Expected 64.3 h total (6.43 h per session);
conservative x1.25.
