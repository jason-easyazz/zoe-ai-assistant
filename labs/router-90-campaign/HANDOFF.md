# HANDOFF — finish the router-90 campaign

State as of 2026-07-14 ~10:30 AWST:

- Grammar evals DONE (see README scoreboard): best measured = 75.3%
  (hybrid functok-gb). Grammar is not the 90% lever; sibling data is.
- `data/train_sibling.jsonl` (275 ex) committed; generator
  `labs/functiongemma-finetune/build_sibling_dataset.py`.
- **e3 is still training** (old dataset, 2 epochs from e1-merged), pid was
  3830274, nice 19, ~250 s/step, 352 steps → ETA ~15–20 h from 08:17 start.
  Log: `/home/zoe/zoe-idle-trainer.log`. Its idle-pause supervisor was
  killed (full-speed regime); the training pgid itself is
  classifier-protected — only Jason/operator kills it early.
  Output dir: `/home/zoe/assistant/.claude/worktrees/agent-a45686e7b068b49f1/labs/functiongemma-finetune/runs/functok-e3`
- llama-server (:11434 brain) is STOPPED for the training window.
  Bring it back when training windows end:
  `systemctl --user start llama-server.service && curl -s localhost:11434/health`

## Exact next commands (any session, repo root of a worktree on this branch)

1. Poll e3: `tail -2 /home/zoe/zoe-idle-trainer.log` until
   `training exited rc=0`.
2. Export e3 GGUF (E3=<e3 output dir above>):
   ```
   cd labs/functiongemma-finetune
   LLAMA_CPP=/home/zoe/llama.cpp ./export_gguf.sh $E3/merged functok-e3-q8.gguf
   cp <produced gguf> /home/zoe/models/lab/functiongemma-270m-zoe-functok-e3-Q8_0.gguf
   ```
3. Eval e3, three ways (each writes results/*.json; ~2 min each):
   ```
   G=/home/zoe/models/lab/functiongemma-270m-zoe-functok-e3-Q8_0.gguf
   python3 labs/functiongemma-finetune/run_eval.py --gguf $G --variant functok --tag functok-e3
   GGUF_FUNCTOK=$G python3 labs/router-90-campaign/run_grammar_eval.py --config functok-ga --tag e3-ga
   GGUF_FUNCTOK=$G python3 labs/router-90-campaign/run_grammar_eval.py --config functok-gb --tag e3-gb
   ```
4. If e3-gb ≥ 90%: declare victory (config = SetFit logreg top-3 shortlist +
   e3 functok decoder + shortlist GBNF grammar). Update README scoreboard.
5. If < 90%: run the definitive training (~5–6 h, warm-start from the BEST
   checkpoint — point MERGED at e3's merged if e3 beat e1):
   ```
   systemctl --user stop llama-server.service   # free RAM/cores
   MERGED=$E3/merged nohup bash labs/router-90-campaign/run_definitive_training.sh \
       >> /home/zoe/zoe-definitive-trainer.log 2>&1 &
   ```
   Then repeat steps 2–3 with the definitive merged dir /
   `functiongemma-270m-zoe-functok-def-Q8_0.gguf`, tags `def-ga`/`def-gb`.
   Restart llama-server after.
6. Update `README.md` scoreboard + verdict; commit results; Greptile loop
   to merge.

Sequencing decision record: the plan was to kill e3 and go straight to the
definitive run once the sibling set existed (e3 trains on the old data), but the
kill was classifier-denied (Jason's authorization was conditional on e3
finishing). e3 therefore runs to completion and doubles as the
"epochs-only vs epochs+data" attribution point.
