# Human verification of the paper's claims

A small local tool for checking, item by item, every claim in the JURIX 2026 paper that
rests on automatically produced material. Each task asks one yes/no question per item.

| Task | Items | Question | Feeds the paper |
|---|---:|---|---|
| `gold` | 65 | Is the expected answer right for this input? | Validity of the citation benchmark |
| `instructions` | 147 | Does the extracted instruction match the source block? | Instruction precision (new) |
| `parse_failures` | 14 | Is there really no operative instruction in the stored text? | Error analysis, Section 6.3 |
| `links` | 200 (sample) | Is the citation linked to the right act? | Linkage precision (new) |
| `gazette` | 100 (sample) | Is the year+number candidate the cited act? | Gazette matching, Section 6.2 |
| `segmentation` | 20 (sample) | Are the considerations split correctly? (open the source link) | Case-law segmentation |

## Use

```bash
python annotation/export_tasks.py --db lexsr_corpus_v1.0.sqlite   # once; the released corpus
python annotation/serve.py                    # open http://127.0.0.1:8765
python annotation/summarize.py --md annotation/RESULTS.md                          # any time
```

Type your name, pick a task, and judge with the keyboard: `1` correct, `2` wrong,
`3` unsure, `S` skip, arrows to move, `N` next unlabelled, `E` to write a note. Use the
note for the right answer when something is wrong; that is what turns a check into a
correction.

Every verdict is appended to `annotation/labels/<task>.jsonl` the moment you give it
(flushed to disk), with your name and a timestamp. Stop and resume whenever you like;
changing a verdict adds a new line and the latest one counts. Stop the server with Ctrl+C.

Samples are drawn with a fixed seed, so re-running the export on the same database gives
the same items and existing labels stay attached.

`summarize.py` reports, per task, the share judged correct with a 95% Wilson interval, and
lists every wrong or unsure item with its note.
