# edpack

Turn a unit's **Ed Lessons** into an offline folder of Markdown and PDFs, plus a ready-to-drag **NotebookLM upload** folder. One command, no browser scraping, no AI in the pipeline.

Built by a Monash student, for Monash students, because when you are on a time crunch and there are twelve weeks of slides to search through, human eyes are the bottleneck. NotebookLM is good at summarising and finding the exact wording you need, but only if you can get the material into it cleanly. That is the whole job of this tool.

> **Scope:** personal use. Tested only on Monash units hosted on `edstem.org/au`. It should work for any school that uses Ed Lessons, but that has never been tried.

## What you get

```
FIT2109/
├── README.md                                 index of everything
├── Week 1 - Introduction to the Shell/
│   ├── W1 Pre-Class - .../
│   │   ├── 01 - 1.1 - History and Context.md
│   │   ├── ...
│   │   ├── 17 - Check Your Understanding.md  quiz with answers marked
│   │   └── images/
│   ├── W1 Workshop/
│   │   ├── 02 - workshop 1 slides.pdf        original PDF
│   │   ├── 02 - workshop 1 slides.md         text version of the same PDF
│   │   └── ...
│   └── W1 Applied Session/
├── Week 2 - .../
└── NotebookLM upload/                        drag this whole folder in
    ├── W01 - Introduction to the Shell.md    all of week 1's text, merged
    ├── W01 - Figures and images.pdf          every image from the readings, captioned
    ├── W01 W1 Workshop - workshop 1 slides.pdf
    └── ...
```

Every Ed slide type is handled:

| Ed slide type | What edpack does |
|---|---|
| Document (Ed's own editor) | Converted to Markdown. Images downloaded next to it. |
| Web page (embedded reading) | Fetched and converted to Markdown. Images downloaded. |
| PDF | Downloaded as-is, plus a `.md` text version beside it. |
| Quiz | Every question and option written out. Answers marked when the unit releases solutions, or when you have already answered correctly. Handles multiple choice, multi-select, true/false, reorder, short answer and open questions. |
| Code challenge | Description and any explanation saved. |
| Video / embed | Link recorded, so nothing is silently lost. |

At the end of every run an **audit** compares what Ed said exists against what landed on disk, and lists anything odd: failed slides, PDFs with no text, suspiciously short files, broken image links. You read ten lines instead of checking two hundred files.

## Requirements

- Python 3.9 or newer
- An Ed account that can see the unit
- Internet for the download step. Everything after that works offline.

## Install

```bash
git clone https://github.com/imosleo/edpack.git
cd edpack
pip install .
```

Then, from any folder:

```bash
edpack
```

If Windows says `edpack` is not recognised, pip printed the folder it installed the command into (something like `...\Python\Python3xx\Scripts`). Add that folder to your PATH once, or run `python -m edpack` instead.

## Getting your Ed API token

edpack never sees your password. It uses a personal API token that Ed issues to you.

1. Log in to Ed in your browser.
2. Open **https://edstem.org/au/settings/api-tokens** (swap `au` for `us` or `eu` if that is where your school's Ed lives).
3. Click **New token**, give it a name like `edpack`, and copy it.
4. Run `edpack` for the first time. It will ask for the token and save it to `~/.edpack/config.json`.

Keep the token private. It grants the same read access to Ed that you have. If it ever leaks, delete it on that same page and make a new one; run `edpack setup` to store the replacement.

## Using it

### Interactive (the normal way)

```
edpack
```

It lists your units, asks which weeks you want, where to save, then shows a progress bar and finishes with the audit report and the two paths you need. It stays open afterwards so you can archive another unit or reopen the folder.

### Scripted

```bash
edpack run   --course 39026 --weeks 1-8 --out ./FIT2109   # everything in one go
edpack fetch --course 39026 --weeks 9   --out ./FIT2109   # download only
edpack build --out ./FIT2109                              # raw data -> folders (works offline)
edpack nblm  --out ./FIT2109                              # rebuild the NotebookLM folder
edpack audit --out ./FIT2109                              # re-run the checks
```

The course id is the number in the Ed URL: `edstem.org/au/courses/39026/lessons`.

### Uploading to NotebookLM

1. Open NotebookLM and create a notebook.
2. **Add sources**, then **Upload files**.
3. Select everything inside the `NotebookLM upload` folder and open.

Readings and quizzes go in as Markdown, which is the cleanest text input. Slides and handouts go in as the original PDFs so diagrams and screenshots survive. The figures PDF carries the reading-page images with captions, since a Markdown upload cannot include pictures.

## Limitations

- **NotebookLM source caps.** Free accounts allow 50 sources per notebook, NotebookLM Pro allows 300. A full semester of one unit is typically 40 to 60 sources, so on a free account you may need to split it into two notebooks or upload only the weeks you need. Check the counter at the bottom of the upload dialog.
- **Quiz answers depend on the unit.** If the unit releases solutions, every answer is marked. If not, only the questions you have already answered correctly are marked. edpack does not attempt quizzes for you.
- **Files hosted outside Ed** (Moodle zips, Panopto videos, YouTube) are recorded as links, not downloaded. They are listed at the bottom of the archive's README.
- **Images inside Markdown** are saved beside the file, but NotebookLM cannot read them from a `.md` upload. That is what the figures PDF is for.
- **Scanned PDFs** come through with no text. The audit flags them.
- **New Ed formats.** Ed adds slide types and content blocks from time to time. edpack does not drop unknown content: unknown blocks keep their text, unknown slide and question types are written out with their raw data, and the audit names them. They just will not look pretty until the converter learns about them.

## Something looks wrong?

If the audit flags something, a file comes out garbled, or your unit uses a format edpack has not seen, open an issue on this repo with the slide type or a small sample of the output. Please do not paste your token or anything private. I will see if I can fix it, though this is a side project, so no promises on timing.

## How it works, briefly

`fetch` calls Ed's JSON API with your token and saves everything to `_raw/ed_dump.json`, plus a byte cache of every page and PDF. `build` walks that dump and writes the folder tree, converting Ed's XML document format and embedded HTML pages to Markdown and extracting PDF text with PyMuPDF. `nblm` merges each week's text into one file and builds the figures PDF. `audit` cross-checks the lot. Because the raw dump and cache are kept, everything after `fetch` can be rerun with no internet.

No language model is involved anywhere. It is deterministic Python, so the same input gives the same output every time.

## License

MIT. Use it, fork it, fix it. Just keep the token out of your commits.
