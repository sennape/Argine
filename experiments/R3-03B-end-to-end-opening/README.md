# R3-03B end-to-end opening / freeze

`E2E_FREEZE.json` opens the 8 GiB primary end-to-end campaign: N=20 ABBA blocks, 80 counted launches, no warmups, timing ON, no event trace, graceful shutdown. Freeze SHA-256: `eb3e87b6d2b7c1457f7a4bbb9d37308eb0a4b4b7adf2ab415f039826bc505173`.

No end-to-end measurement may precede the commit of this file. Nothing has been acquired.

Windows CMD command (run only with browsers/streaming closed):

```
C:\Users\alpel\anaconda3\python.exe C:\Users\alpel\Desktop\Argine4\tools\r3_03b_e2e.py --runtime C:\Users\alpel\Desktop\Argine4-r3build-r3-03b\llama.cpp\build-r3-03b\bin\llama-server.exe --runtime-sha256 8d2e1def2bd46f9f395a9e5662bd04cc8eafad6c77a337de2abcff1f82685ba5 --freeze-sha256 eb3e87b6d2b7c1457f7a4bbb9d37308eb0a4b4b7adf2ab415f039826bc505173 --out-dir C:\Users\alpel\Desktop\Argine4-r3runs\R3-03B-e2e --attest-clean-environment
```

Offline analysis afterwards (no model): `tools\r3_03b_e2e.py --analyze --out-dir <dir>`.

## Amendment 1 (shutdown incident, resume)

`E2E_FREEZE_AMENDMENT_1.json` (freeze SHA-256 `8d9bf840ddd734fde55ac5ece8dda150f967ff9915b87299dc348c76cbaa72eb`) supersedes the driver of commit bee595c with a resume-safe driver; scientific content is unchanged. The command above now also requires `--amendment-sha256 8d9bf840ddd734fde55ac5ece8dda150f967ff9915b87299dc348c76cbaa72eb`, and `--resume` continues the existing campaign directory. See `experiments/R3-03B-e2e-shutdown-incident/INCIDENT.json`.
