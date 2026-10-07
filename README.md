# edpack

Turn a unit's **Ed Lessons** into an offline folder of Markdown and PDFs, plus a ready-to-drag **NotebookLM upload** folder. One command, no browser scraping, no AI in the pipeline.

Built by a Monash student, for Monash students, because when you are on a time crunch and there are twelve weeks of slides to search through, human eyes are the bottleneck. NotebookLM is good at summarising and finding the exact wording you need, but only if you can get the material into it cleanly. That is the whole job of this tool.

> **Scope:** personal use. Tested only on Monash units hosted on `edstem.org/au`. It should work for any school that uses Ed Lessons, but that has never been tried.

## What you get

```
FIT2109/
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

## Updating

New versions are listed under [Releases](https://github.com/imosleo/edpack/releases). To check which one you have:

```bash
pip show edpack
```

If you installed with `git clone` as above, go back into that folder and pull the new code:

```bash
cd edpack
git pull
pip install .
```

If you no longer have that folder, update straight from GitHub instead:

```bash
pip install --upgrade git+https://github.com/imosleo/edpack.git
```

Your saved token and remembered folders live in `~/.edpack/config.json`, so they carry over. Archives made with an older version keep working: point the new version at the same unit folder and it adds to what is there.

## Getting your Ed API token

edpack never sees your password. It uses a personal API token that Ed issues to you.

1. Log in to Ed in your browser.
2. Paste this into the address bar: **https://edstem.org/au/settings/api-tokens** (swap `au` for `us` or `eu` if that is where your school's Ed lives).
   The page is **not linked from the Settings menu** because Ed's API is still in beta, so you have to open it by URL. Once you are on it, an "API Tokens" entry appears in the left menu.
3. Click **Create Token** and copy the value. Ed shows it only once; afterwards the list shows just the first few characters.
4. Run `edpack` for the first time. It will ask for the token and save it to `~/.edpack/config.json`.

Keep the token private. It grants the same read access to Ed that you have. If it ever leaks, delete it on that same page and make a new one; run `edpack setup` to store the replacement.

## Using it

### Interactive (the normal way)

```
edpack
```

It lists your units, asks which weeks you want and which folder is that unit's archive (pick one from the list or paste any path), then shows a progress bar and finishes with the audit report and the two paths you need. It stays open afterwards so you can archive another unit or reopen the folder.

The week folders are named by Ed (e.g. `Week 9 - Debugging - Systematic Bug Hunting`) and go straight into the folder you picked. Run it again later with the next week and the same folder: the new week is added alongside the old ones, and the NotebookLM upload and audit cover every week in the folder. Picking a week you already have refreshes it so it matches Ed exactly: new slides are added, a renamed week or lesson has its folder renamed (your own files inside come along), and anything Ed no longer has is moved to `_raw/replaced/<date>/` rather than deleted. Files you add yourself are never touched. edpack remembers the folder per unit and offers it first next time.

### Scripted

```bash
edpack run   --course 39026 --weeks 1-8 --out ./FIT2109   # everything in one go
edpack fetch --course 39026 --weeks 9   --out ./FIT2109   # download only
edpack build --out ./FIT2109                              # raw data -> folders (works offline)
edpack nblm  --out ./FIT2109                              # rebuild the NotebookLM folder
edpack moodle --out ./FIT2109                             # download files the slides link to on Moodle
edpack audit --out ./FIT2109                              # re-run the checks
```

The course id is the number in the Ed URL: `edstem.org/au/courses/39026/lessons`.

### Files on Moodle

Many slides only say "download the zip from here" and link to Moodle. After building, edpack offers to download those files into a `moodle` folder inside the lesson's folder. They are part of your offline archive and are **not** copied into the NotebookLM upload folder.

Monash has Moodle's app API switched off, so there is no permanent token like Ed's. Instead you lend edpack your browser's login session for that run:

1. Open Moodle in your browser and make sure you are logged in.
2. Press F12. Chrome/Edge: **Application** tab > **Cookies**. Firefox: **Storage** tab > **Cookies**.
3. Click the Moodle site, find the row named `MoodleSession` and copy its **Value**.
4. Paste it when edpack asks. Press Enter instead to skip.

edpack saves the value in `~/.edpack/config.json` next to your Ed token and reuses it, so you only paste it again once Moodle has ended that session (you logged out, or it timed out). edpack checks it at the start of each run and asks for a fresh one when it has expired. Files already downloaded are skipped next time, except in weeks you pick again: those are re-checked, and if the lecturer replaced a file the new one is saved and the old one moved to `_raw/replaced/`. For scripted runs, set the `MOODLE_SESSION` environment variable instead.

Treat that value like a password: until the session ends, anyone holding it is logged in to Moodle as you. Logging out of Moodle in your browser ends it immediately.

#### Skip the copying: let Chrome hand it over

Chrome and Edge encrypt their cookie files, so edpack cannot read the session from disk. Instead, a tiny extension that can see only the Moodle site passes it to edpack whenever you log in, through Chrome's official native messaging channel. One-time setup per computer:

```bash
edpack moodle-setup
```

Then in Chrome open `chrome://extensions` (Edge: `edge://extensions`), turn on **Developer mode**, click **Load unpacked** and choose the folder edpack printed (`~/.edpack/chrome-extension`). Open Moodle once and log in as usual.

From then on, being logged in to Moodle in Chrome is enough. When the session has ended, edpack asks you to open Moodle in Chrome and press Enter; nothing to copy. The extension is about ten lines of JavaScript (written to that folder, readable before you load it) and asks only for the `cookies` and `nativeMessaging` permissions on your Moodle site. Other universities: `edpack moodle-setup --host your.moodle.site`. To undo: `edpack moodle-setup --remove`, then remove the extension in Chrome.

### Uploading to NotebookLM

1. Open NotebookLM and create a notebook.
2. **Add sources**, then **Upload files**.
3. Select everything inside the `NotebookLM upload` folder and open.

Readings and quizzes go in as Markdown, which is the cleanest text input. Slides and handouts go in as the original PDFs so diagrams and screenshots survive. The figures PDF carries the reading-page images with captions, since a Markdown upload cannot include pictures.

## Limitations

- **NotebookLM source caps.** Free accounts allow 50 sources per notebook, NotebookLM Pro allows 300. A full semester of one unit is typically 40 to 60 sources, so on a free account you may need to split it into two notebooks or upload only the weeks you need. Check the counter at the bottom of the upload dialog.
- **Quiz answers depend on the unit.** If the unit releases solutions, every answer is marked. If not, only the questions you have already answered correctly are marked. edpack does not attempt quizzes for you.
- **Videos** (Panopto, YouTube) are recorded as links, not downloaded. Moodle files are downloaded only if you give edpack your Moodle session (see *Files on Moodle*); otherwise they stay links and the audit counts how many are left.
- **Images inside Markdown** are saved beside the file, but NotebookLM cannot read them from a `.md` upload. That is what the figures PDF is for.
- **Scanned PDFs** come through with no text. The audit flags them.
- **New Ed formats.** Ed adds slide types and content blocks from time to time. edpack does not drop unknown content: unknown blocks keep their text, unknown slide and question types are written out with their raw data, and the audit names them. They just will not look pretty until the converter learns about them.

## Something looks wrong?

If the audit flags something, a file comes out garbled, or your unit uses a format edpack has not seen, open an issue on this repo with the slide type or a small sample of the output. Please do not paste your token or anything private. I will see if I can fix it, though this is a side project, so no promises on timing.

## How it works, briefly

`fetch` calls Ed's JSON API with your token and saves everything to `_raw/ed_dump.json`, plus a byte cache of every page and PDF. `build` walks that dump and writes the folder tree, converting Ed's XML document format and embedded HTML pages to Markdown and extracting PDF text with PyMuPDF. `nblm` merges each week's text into one file and builds the figures PDF. `audit` cross-checks the lot. Because the raw dump and cache are kept, everything after `fetch` can be rerun with no internet.

No language model is involved anywhere. It is deterministic Python, so the same input gives the same output every time.

## References and prior art

Everything edpack relies on is either documented by Ed or observable in your own browser.

- **Ed's API documentation.** On the API Tokens settings page (https://edstem.org/au/settings/api-tokens), the **Learn more about Ed API** button opens Ed's own reference. It states that the API server is `https://edstem.org/api`, that a token is sent as an `Authorization: Bearer <token>` header, and that "Ed APIs are provided as is and may change without notice." The same page labels API access as beta. That documentation covers the user endpoint; edpack uses it to list your courses.
- **The Lessons endpoints** (`/courses/{id}/lessons`, `/lessons/{id}`, `/lessons/slides/{id}`, `/lessons/slides/{id}/questions`, `/challenges/{id}`) are not in that published reference. They were identified by watching the requests Ed's own web app makes when you open a lesson, so they carry exactly the permissions your account already has. Ed may rename them at any time, which is the main way this tool could break.
- **Prior art:** [edapi](https://github.com/smartspot2/edapi) by smartspot2, an unofficial Python integration of the Ed API (GPL-3.0), documents the same token approach and is where it was first shown that the API is stable enough to build on. edpack does not use or include it.
- **NotebookLM source limits** are taken from the counter in NotebookLM's own Add sources dialog (50 per notebook on free, 300 on Pro at the time of writing).

## License

MIT. Use it, fork it, fix it. Just keep your token out of your commits.
