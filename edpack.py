#!/usr/bin/env python3
"""edpack - archive Ed Lessons into an offline, NotebookLM-ready folder.

No AI, no browser. Plain Python + the Ed API.

Usage:
  python edpack.py setup                      # store your Ed API token
  python edpack.py fetch  --course 20603 --weeks 1        # download raw data
  python edpack.py build  --course 20603                  # raw -> folders + Markdown
  python edpack.py nblm   --course 20603                  # NotebookLM upload folder
  python edpack.py moodle --course 20603                  # download files the slides link to on Moodle
  python edpack.py audit  --course 20603                  # self-check report
  python edpack.py run    --course 20603 --weeks 1-8      # all of the above

Get a token at https://edstem.org/<region>/settings/api-tokens
"""
import argparse, hashlib, json, os, re, shutil, sys, time
from urllib.parse import unquote, urljoin, urlparse

try:
    import requests
    from bs4 import BeautifulSoup, NavigableString, Tag
    from markdownify import markdownify as md
    import pymupdf
except ImportError as e:
    sys.exit('Missing dependency: %s\nRun:  pip install requests beautifulsoup4 markdownify pymupdf' % e.name)

# Windows consoles often default to a legacy code page; course names can contain any Unicode.
for _stream in (sys.stdout, sys.stderr):
    try: _stream.reconfigure(encoding='utf-8', errors='replace')
    except Exception: pass

CONFIG_DIR = os.path.join(os.path.expanduser('~'), '.edpack')
CONFIG = os.path.join(CONFIG_DIR, 'config.json')
API = 'https://edstem.org/api'


# ----------------------------------------------------------------------------- helpers
def log(msg): print(msg, flush=True)

def safe(name):
    name = name.replace(':', ' -').replace('/', '-').replace('\\', '-')
    name = re.sub(r'[<>"|?*\x00-\x1f]', '', name)
    return re.sub(r'\s+', ' ', name).strip(' .')[:120]

def load_config():
    if os.path.exists(CONFIG):
        return json.load(open(CONFIG, encoding='utf-8'))
    return {}

def token():
    t = os.environ.get('ED_TOKEN') or load_config().get('token')
    if not t:
        sys.exit('No Ed API token. Run:  python edpack.py setup   (or set ED_TOKEN)')
    return t

def parse_weeks(spec):
    """'1' / '1-8' / '1,3,5' / 'all' -> set of ints (None = all). Raises ValueError on bad input."""
    if not spec or spec.strip().lower() == 'all': return None
    out = set()
    for part in spec.replace(' ', '').split(','):
        if not part: continue
        if '-' in part:
            a, b = part.split('-', 1)
            if int(a) > int(b): raise ValueError('range %s is backwards' % part)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    if not out: raise ValueError('no weeks given')
    return out

RUN_STAMP = time.strftime('%Y-%m-%d %H%M%S')

def retire(root, path):
    """Move an out-of-date file or folder into _raw/replaced/<this run>/ instead of deleting it."""
    rel = os.path.relpath(path, root)
    dst = os.path.join(root, '_raw', 'replaced', RUN_STAMP, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(path, dst)
    log('  out of date, moved to _raw/replaced/%s: %s' % (RUN_STAMP, rel))

def week_of(name):
    m = re.search(r'week\s*(\d+)', name, re.I)
    return int(m.group(1)) if m else None

def same_course(dump, course_id, module_names):
    """Is this existing dump from the given course? Archives made before edpack recorded the
    course id have none, so fall back to matching module names."""
    if dump.get('course') is not None:
        return str(dump['course']) == str(course_id)
    return any(g.get('name') in module_names for g in dump.get('modules', []))

def check_weeks(weeks, available):
    """Return an error message if any requested week is not in the course, else None."""
    if weeks is None: return None
    missing = sorted(weeks - set(available))
    if missing:
        return 'Week %s %s not exist in this course. Available: %s' % (
            ', '.join(map(str, missing)), 'does' if len(missing) == 1 else 'do', ', '.join(map(str, sorted(available))))
    return None


class Ed:
    def __init__(self, tok):
        self.s = requests.Session()
        self.s.headers.update({'Authorization': 'Bearer ' + tok, 'User-Agent': 'edpack/0.4.1'})

    def get(self, path, **kw):
        for attempt in range(3):
            r = self.s.get(API + path, timeout=60, **kw)
            if r.status_code == 429:
                time.sleep(2 + attempt * 2); continue
            if r.status_code in (400, 401) and 'token' in r.text.lower():
                sys.exit('Ed rejected the token (%s). Make a new one at Settings > API tokens.' % r.status_code)
            r.raise_for_status()
            return r.json()
        r.raise_for_status()


class Cache:
    """Byte cache for public files (reading pages, images, PDFs) so rebuilds work offline."""
    def __init__(self, d):
        self.d = d; os.makedirs(d, exist_ok=True)
        self.s = requests.Session(); self.s.headers['User-Agent'] = 'Mozilla/5.0'

    def get(self, url):
        p = os.path.join(self.d, hashlib.sha1(url.encode()).hexdigest())
        if os.path.exists(p):
            return open(p, 'rb').read()
        r = self.s.get(url, timeout=60); r.raise_for_status()
        open(p, 'wb').write(r.content)
        return r.content


# ----------------------------------------------------------------------------- fetch
def cmd_fetch(a):
    ed = Ed(token())
    try:
        weeks = parse_weeks(a.weeks)
    except ValueError as e:
        sys.exit('Bad --weeks value "%s" (%s). Use e.g. 1  or  1-8  or  1,3,5' % (a.weeks, e))
    L = ed.get('/courses/%s/lessons' % a.course)
    modules = L.get('modules') or []
    lessons = L.get('lessons') or []
    dump = {'course': a.course, 'fetched_at': time.strftime('%Y-%m-%d %H:%M'), 'modules': [], 'errors': []}

    # Lessons are grouped by module; modules are usually named "Week N: ...".
    groups = []
    for i, m in enumerate(modules):
        groups.append({'id': m['id'], 'name': m['name'], 'week': week_of(m['name']), 'order': i})
    unmoduled = [l for l in lessons if not any(l.get('module_id') == g['id'] for g in groups)]
    if unmoduled:
        groups.append({'id': None, 'name': 'Other lessons', 'week': None, 'order': len(groups)})

    err = check_weeks(weeks, [g['week'] for g in groups if g['week'] is not None])
    if err:
        sys.exit(err)
    raw = os.path.join(a.out, '_raw'); path = os.path.join(raw, 'ed_dump.json')
    old = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else None
    if old and not same_course(old, a.course, [g['name'] for g in groups]):
        sys.exit('%s already holds an archive of another Ed course (%s). Pick a different folder.' % (a.out, old.get('course') or 'unknown'))
    selected = [g for g in groups if weeks is None or g['week'] in weeks]
    total = sum(l.get('slide_count') or 0 for g in selected
                for l in ([x for x in lessons if x.get('module_id') == g['id']] if g['id'] else unmoduled))
    done = 0
    for g in selected:
        ls = [l for l in lessons if l.get('module_id') == g['id']] if g['id'] else unmoduled
        ls.sort(key=lambda l: (l.get('index') if l.get('index') is not None else 999, l['id']))
        G = {'id': g['id'], 'name': g['name'], 'week': g['week'], 'order': g['order'], 'lessons': []}
        log('\n%s' % g['name'])
        for l in ls:
            log('  %s' % l['title'])
            d = ed.get('/lessons/%s?view=1' % l['id'])['lesson']
            les = {'id': l['id'], 'title': l['title'], 'status': l.get('status'), 'slides': []}
            for s in d.get('slides') or []:
                done += 1
                progress(done, total, s['title'])
                S = {'id': s['id'], 'type': s['type'], 'title': s['title'], 'status': s.get('status')}
                try:
                    S['detail'] = ed.get('/lessons/slides/%s?view=1' % s['id'])['slide']
                    if s['type'] == 'quiz':
                        S['questions'] = ed.get('/lessons/slides/%s/questions' % s['id']).get('questions', [])
                        S['responses'] = ed.get('/lessons/slides/%s/questions/responses' % s['id']).get('responses', [])
                    if s['type'] == 'code' and S['detail'].get('challenge_id'):
                        S['challenge'] = ed.get('/challenges/%s?view=1' % S['detail']['challenge_id']).get('challenge')
                except Exception as e:
                    dump['errors'].append({'module': g['name'], 'slide': s['id'], 'title': s['title'], 'error': str(e)})
                les['slides'].append(S)
            G['lessons'].append(les)
        dump['modules'].append(G)

    n = sum(len(l['slides']) for g in dump['modules'] for l in g['lessons'])
    log('\nfetched %d modules, %d slides, %d errors' % (len(dump['modules']), n, len(dump['errors'])))

    # Add to an existing archive of the same course instead of replacing it, so fetching
    # week 9 into a folder that already has weeks 1-8 keeps all nine in the index and NotebookLM pack.
    os.makedirs(raw, exist_ok=True)
    if old:
        key = lambda g: g.get('id') or g['name']   # Ed's module id survives a rename
        new = {key(g): g for g in dump['modules']}
        kept = [g for g in old['modules'] if key(g) not in new]
        def follow(src, dst):
            """Ed renamed a week or lesson: rename its folder so your own files and Moodle downloads come along."""
            if os.path.isdir(src) and os.path.normcase(src) != os.path.normcase(dst):
                if os.path.exists(dst): retire(a.out, src)
                else:
                    os.rename(src, dst); log('  renamed on Ed: %s -> %s' % (os.path.relpath(src, a.out), os.path.relpath(dst, a.out)))
        for o in old['modules']:
            n = new.get(key(o))
            if not n: continue
            gdir = os.path.join(a.out, safe(n['name']))
            follow(os.path.join(a.out, safe(o['name'])), gdir)
            now = {l['id']: l for l in n['lessons']}
            for l in o['lessons']:
                p = os.path.join(gdir, safe(l['title']))
                if l.get('id') in now:
                    follow(p, os.path.join(gdir, safe(now[l['id']]['title'])))
                elif os.path.isdir(p):
                    retire(a.out, p)                  # lesson removed from Ed
        order = {key(g): g['order'] for g in groups}
        dump['modules'] = sorted(kept + dump['modules'], key=lambda g: order.get(key(g), g.get('order', 999)))
        fresh = {g['name'] for g in dump['modules']}
        dump['errors'] = [e for e in old.get('errors', []) if e.get('module') not in fresh] + dump['errors']
        if kept:
            log('kept %d module(s) already in this folder: %s' % (len(kept), ', '.join(g['name'] for g in kept)))
    json.dump(dump, open(path, 'w', encoding='utf-8'), indent=1)
    # Only what was just downloaded gets rebuilt; weeks already in the folder keep their files.
    a.fresh = {g['name'] for g in selected}


def progress(done, total, label=''):
    total = max(total, done, 1)
    bar = '#' * int(30 * done / total) + '.' * (30 - int(30 * done / total))
    label = (label[:40] + '...') if len(label) > 43 else label
    sys.stdout.write('\r    [%s] %3d/%-3d %-45s' % (bar, done, total, label))
    sys.stdout.flush()
    if done >= total: sys.stdout.write('\n')


# ----------------------------------------------------------------------------- Ed XML -> Markdown
def ed_inline(node):
    out = []
    for c in node.children:
        if isinstance(c, NavigableString):
            out.append(str(c)); continue
        if not isinstance(c, Tag): continue
        n = c.name
        if n == 'edlink':
            t = ed_inline(c).strip() or c.get('href', ''); out.append('[%s](%s)' % (t, c.get('href', '')))
        elif n == 'bold': out.append('**' + ed_inline(c).strip() + '**')
        elif n == 'italic': out.append('*' + ed_inline(c).strip() + '*')
        elif n == 'strike': out.append('~~' + ed_inline(c) + '~~')
        elif n == 'code': out.append('`' + c.get_text() + '`')
        elif n == 'break': out.append('  \n')
        elif n == 'math': out.append('$' + c.get_text() + '$')
        elif n == 'image': out.append('![%s](%s)' % (c.get('alt', ''), c.get('src', '')))
        else: out.append(ed_inline(c))
    return ''.join(out)

BLOCK_TAGS = {'paragraph', 'heading', 'snippet', 'pre', 'code-block', 'list', 'callout', 'table', 'figure',
              'file', 'video', 'embed', 'iframe', 'youtube', 'web-snippet', 'spoiler', 'details', 'image'}

def has_block_children(node):
    return any(isinstance(c, Tag) and c.name in BLOCK_TAGS for c in node.children)

import html as _html
def web_snippet_to_md(node):
    """Ed 'web snippet' blocks hold raw HTML (usually a video iframe). Keep the URLs, drop the markup."""
    raw = _html.unescape(node.get_text())
    urls = re.findall(r'(?:src|href)=["\']([^"\']+)["\']', raw)
    if urls:
        return ''.join('Embedded media: %s\n\n' % _html.unescape(u) for u in urls)
    txt = BeautifulSoup(raw, 'html.parser').get_text(' ').strip()
    return (txt + '\n\n') if txt else ''

def ed_block(node, depth=0):
    out = []
    # a container whose children are only text and inline tags is really one paragraph
    if not has_block_children(node) and any(isinstance(c, Tag) for c in node.children):
        return ed_inline(node).strip() + '\n\n'
    for c in node.children:
        if isinstance(c, NavigableString):
            if c.strip(): out.append(c.strip() + '\n\n')
            continue
        n = c.name
        if n == 'paragraph': out.append(ed_inline(c).strip() + '\n\n')
        elif n == 'web-snippet': out.append(web_snippet_to_md(c))
        elif n in ('snippet', 'pre', 'code-block') and c.find('snippet-file'):
            for f in c.find_all('snippet-file'):
                if f.get_text().strip():
                    out.append('```%s\n%s\n```\n\n' % (f.get('language', '') or '', f.get_text().rstrip()))
        elif n == 'heading':
            out.append('#' * min(int(c.get('level', 2)) + 1, 6) + ' ' + ed_inline(c).strip() + '\n\n')
        elif n in ('snippet', 'pre', 'code-block'):
            body = '\n'.join(l.get_text() for l in c.find_all('snippet-line')) if c.find('snippet-line') else c.get_text()
            out.append('```%s\n%s\n```\n\n' % (c.get('language', '') or '', body.rstrip()))
        elif n == 'list':
            num = c.get('style', 'bullet') == 'number'
            for k, it in enumerate(c.find_all('list-item', recursive=False), 1):
                lines = ed_block(it, depth + 1).strip().split('\n')
                out.append('  ' * depth + ('%d.' % k if num else '-') + ' ' + lines[0] + '\n')
                for ln in lines[1:]: out.append(('  ' * depth + '  ' + ln if ln.strip() else '') + '\n')
            out.append('\n')
        elif n == 'callout':
            out.append('> **%s**\n> %s\n\n' % (c.get('type', 'info').upper(), ed_block(c).strip().replace('\n', '\n> ')))
        elif n == 'table':
            for i, r in enumerate(c.find_all('table-row')):
                cells = [ed_block(x).strip().replace('\n', ' ') for x in r.find_all('table-cell')]
                out.append('| ' + ' | '.join(cells) + ' |\n')
                if i == 0: out.append('|' + '---|' * len(cells) + '\n')
            out.append('\n')
        elif n in ('image', 'figure'):
            img = c if n == 'image' else (c.find('image') or c)
            out.append('![%s](%s)\n\n' % (img.get('alt', ''), img.get('src', '')))
            cap = c.find('caption') if n == 'figure' else None
            if cap: out.append('*' + ed_inline(cap).strip() + '*\n\n')
        elif n == 'file':
            out.append('Attachment: [%s](%s)\n\n' % (c.get('name') or c.get('href'), c.get('href') or c.get('url', '')))
        elif n in ('video', 'embed', 'iframe', 'youtube'):
            out.append('Embedded media: %s\n\n' % (c.get('src') or c.get('href') or c.get('id', '')))
        elif n == 'break': out.append('\n')
        else:
            inner = ed_block(c, depth)
            out.append(inner if inner.strip() else ed_inline(c) + '\n\n')
    return ''.join(out)

def ed_to_md(xml, img_sink=None):
    if not xml: return ''
    xml = re.sub(r'<link(\s|>)', r'<edlink\1', xml).replace('</link>', '</edlink>')
    soup = BeautifulSoup(xml, 'html.parser')
    txt = ed_block(soup.find('document') or soup)
    txt = re.sub(r'\n{3,}', '\n\n', txt).strip() + '\n'
    return img_sink(txt) if img_sink else txt


# ----------------------------------------------------------------------------- builder
class Builder:
    def __init__(self, out, only=None):
        self.out = out; self.only = only   # module names to (re)build; None = all
        self.raw = os.path.join(out, '_raw')
        self.dump = json.load(open(os.path.join(self.raw, 'ed_dump.json'), encoding='utf-8'))
        self.cache = Cache(os.path.join(self.raw, 'cache'))
        self.stats = {'slides': 0, 'written': 0, 'failed': [], 'external': [], 'webpages': []}

    # images referenced from Ed documents/quizzes live on static.*.edusercontent.com
    def localise_images(self, text, img_dir):
        def repl(m):
            url = m.group(2)
            try:
                data = self.cache.get(url)
                ext = {b'\x89PNG': '.png', b'\xff\xd8\xff': '.jpg', b'GIF8': '.gif', b'RIFF': '.webp'}.get(data[:4], '') or \
                      ('.jpg' if data[:3] == b'\xff\xd8\xff' else '') or ('.svg' if b'<svg' in data[:300] else '')
                fn = 'ed-' + os.path.basename(urlparse(url).path) + ext
                os.makedirs(img_dir, exist_ok=True)
                open(os.path.join(img_dir, fn), 'wb').write(data)
                return '![%s](images/%s)' % (m.group(1), fn)
            except Exception:
                return m.group(0)
        return re.sub(r'!\[([^\]]*)\]\((https://static\.[a-z.]*edusercontent\.com/[^)\s]+)\)', repl, text)

    def webpage(self, url, img_dir):
        raw = self.cache.get(url).decode('utf-8', 'replace')
        soup = BeautifulSoup(raw, 'html.parser')
        for t in soup(['script', 'style', 'nav', 'noscript', 'header', 'footer']): t.decompose()
        body = soup.find('main') or soup.find('article') or soup.find(class_='page') or soup.body or soup
        plain_words = len(body.get_text(' ').split())
        for pre in body.find_all('pre'):
            text = pre.get_text(); code = pre.find('code'); lang = ''
            if code:
                m = re.search(r'language-(\w+)', ' '.join(code.get('class', []))); lang = m.group(1) if m else ''
            pre.clear(); pre.string = text; pre['data-lang'] = lang
        for div in body.find_all(class_=re.compile(r'callout|admonition|note|warning', re.I)):
            if div.name == 'div': div.name = 'blockquote'
        for img in body.find_all('img'):
            src = img.get('src')
            if not src: continue
            full = urljoin(url, src)
            try:
                data = self.cache.get(full)
                fn = safe(os.path.basename(urlparse(full).path)) or 'image'
                os.makedirs(img_dir, exist_ok=True)
                open(os.path.join(img_dir, fn), 'wb').write(data)
                img['src'] = 'images/' + fn
            except Exception:
                img['src'] = full
        for fr in body.find_all(['iframe', 'video', 'source']):
            src = urljoin(url, fr.get('src')) if fr.get('src') else '(unknown)'
            self.stats['external'].append(src)
            fr.replace_with(soup.new_string('\n\nEmbedded media: %s\n\n' % src))
        for a in body.find_all('a', href=True): a['href'] = urljoin(url, a['href'])
        text = md(str(body), heading_style='ATX', bullets='-',
                  code_language_callback=lambda el: el.get('data-lang', '') if el else '')
        text = re.sub(r'\n{3,}', '\n\n', text).strip() + '\n'
        md_words = len(re.sub(r'[#*`>|_\-]', ' ', text).split())
        self.stats['webpages'].append((url, plain_words, md_words))
        return '<!-- source: %s -->\n\n%s' % (url, text)

    def quiz(self, s, img_dir):
        """Render every Ed question type. The answer comes from, in order of preference:
        the course-released solution, or the student's own response marked correct."""
        sink = lambda t: self.localise_images(t, img_dir)
        doc = lambda x: ed_to_md(x, sink).strip() if isinstance(x, str) and x.startswith('<document') else str(x if x is not None else '').strip()
        L = lambda k: chr(65 + k)
        out = ['# ' + s['title'], '', '_Quiz mode: %s_' % s['detail'].get('mode'), '']
        resp = {r['question_id']: r for r in s.get('responses', [])}
        qs = sorted(s.get('questions', []), key=lambda q: q.get('index', 0))
        known = 0
        body = []
        for i, q in enumerate(qs, 1):
            d = q['data']; t = d.get('type'); r = resp.get(q['id']); rd = r.get('data') if r else None
            sol = d.get('solution')
            body += ['', '## Question %d' % i + ('  (%s)' % t if t not in ('multiple-choice',) else ''), '', doc(d.get('content', ''))]
            answer_line = None
            if t == 'multiple-choice':
                chosen = set(rd) if isinstance(rd, list) else set()
                correct = set(sol) if isinstance(sol, list) else (chosen if r and r.get('correct') else set())
                for k, ans in enumerate(d.get('answers', [])):
                    mark = ''
                    if k in correct: mark = '  <-- CORRECT'
                    elif k in chosen: mark = '  <-- your answer, marked wrong'
                    body.append('- **%s.** %s%s' % (L(k), doc(ans).replace('\n', ' '), mark))
                if correct:
                    src = 'course-released solution' if isinstance(sol, list) else 'confirmed correct on Ed'
                    answer_line = '**Answer: %s** (%s)' % (', '.join(L(k) for k in sorted(correct)), src)
                    if d.get('multiple_selection'): answer_line += '  _(select all that apply)_'
            elif t == 'true-false':
                if isinstance(sol, bool): answer_line = '**Answer: %s** (course-released solution)' % ('True' if sol else 'False')
                elif r and r.get('correct') and isinstance(rd, bool): answer_line = '**Answer: %s** (confirmed correct on Ed)' % ('True' if rd else 'False')
                if isinstance(rd, bool): body.append('_Your answer: %s_' % ('True' if rd else 'False'))
            elif t == 'reorder':
                items = d.get('items', [])
                body.append('Items (as shown):'); body += ['- %s' % doc(x) for x in items]
                if isinstance(sol, list):
                    answer_line = '**Correct order:** ' + ' > '.join(doc(items[k]) for k in sol if k < len(items)) + ' (course-released solution)'
                elif isinstance(rd, list) and r.get('correct'):
                    answer_line = '**Correct order:** ' + ' > '.join(doc(items[k]) for k in rd if k < len(items)) + ' (confirmed correct on Ed)'
            elif t == 'short-answer':
                if sol: answer_line = '**Answer:** %s (course-released solution)' % doc(sol)
                if rd: body.append('_Your answer: %s_' % doc(rd if isinstance(rd, str) else json.dumps(rd)))
            elif t == 'general':   # free-text / discussion question
                if isinstance(rd, dict) and rd.get('content'): body += ['', '**Your answer:**', '', doc(rd['content'])]
                if sol: answer_line = '**Model answer:**\n\n' + doc(sol)
            else:
                body.append('_Unsupported question type "%s"; raw data:_\n\n```json\n%s\n```' % (t, json.dumps(d, indent=1)[:2000]))
            if answer_line:
                known += 1; body += ['', answer_line]
            elif t in ('short-answer', 'general') and not d.get('assessed'):
                known += 1; body += ['', '_Open question, not assessed._']
            else:
                body += ['', '**Status:** answer not known yet (not answered, or answered wrong).']
            if d.get('explanation') and doc(d['explanation']): body += ['', '**Explanation:** ' + doc(d['explanation'])]
        out.append('_%d of %d questions have a known answer._\n' % (known, len(qs)))
        return '\n'.join(out + body) + '\n'

    def code(self, s, img_dir):
        sink = lambda t: self.localise_images(t, img_dir)
        c = s.get('challenge') or {}
        out = ['# ' + s['title'], '', '_Ed code challenge, type: %s, language: %s_' % (c.get('type') or '-', c.get('language') or '-'), '']
        a = ed_to_md(s['detail'].get('content') or '', sink); b = ed_to_md(c.get('content') or '', sink)
        if a.strip(): out.append(a)
        if b.strip() and b.strip() != a.strip(): out += ['## Challenge description', '', b]
        if c.get('explanation'): out += ['## Explanation', '', ed_to_md(c['explanation'], sink)]
        if not (a.strip() or b.strip()): out.append('_No written description; interactive workspace only._')
        return '\n'.join(out) + '\n'

    def pdf_to_md(self, path, title):
        doc = pymupdf.open(path)
        out = ['# ' + title, '', '_Converted from PDF (%d pages)_' % len(doc), '']
        for i, page in enumerate(doc, 1):
            t = re.sub(r'\n{3,}', '\n\n', re.sub(r'[ \t]+\n', '\n', page.get_text('text').strip()))
            out += ['## Page %d' % i, '', t or '_(no extractable text on this page; see the PDF)_', '']
        return '\n'.join(out)

    def build(self):
        index = ['# %s - Ed Lessons offline archive' % self.dump.get('course', ''), '', '_Exported %s_' % self.dump.get('fetched_at', '')]
        for g in self.dump['modules']:
            gdir = os.path.join(self.out, safe(g['name'])); os.makedirs(gdir, exist_ok=True)
            index += ['', '## ' + g['name']]
            for l in g['lessons']:
                ldir = os.path.join(gdir, safe(l['title'])); os.makedirs(ldir, exist_ok=True)
                img_dir = os.path.join(ldir, 'images')
                index += ['', '### ' + l['title'], '']
                lidx = ['# ' + l['title'], '', '_%s_' % g['name'], '']
                made = set()
                for n, s in enumerate(l['slides'], 1):
                    self.stats['slides'] += 1
                    base = '%02d - %s' % (n, safe(s['title'])); det = s.get('detail') or {}
                    if self.only is not None and g['name'] not in self.only:
                        have = [f for f in (base + '.pdf', base + '.md', base + ' (FAILED).md') if os.path.exists(os.path.join(ldir, f))]
                        if have:   # week already in the folder and not re-downloaded: keep its files
                            self.stats['written'] += 1
                            lidx.append('- [%s](<%s>)' % (s['title'], have[0]))
                            index.append('- [%s](<%s/%s/%s>)' % (s['title'], safe(g['name']), safe(l['title']), have[0]))
                            continue
                    try:
                        if s['type'] == 'webpage':
                            fn = base + '.md'; text = self.webpage(det['url'], img_dir)
                        elif s['type'] == 'pdf':
                            fn = base + '.pdf'
                            open(os.path.join(ldir, fn), 'wb').write(self.cache.get(det['file_url']))
                            open(os.path.join(ldir, base + '.md'), 'w', encoding='utf-8').write(self.pdf_to_md(os.path.join(ldir, fn), s['title']))
                            text = None
                        elif s['type'] == 'document':
                            fn = base + '.md'; text = '# %s\n\n%s' % (s['title'], ed_to_md(det.get('content', ''), lambda t: self.localise_images(t, img_dir)))
                            self.stats['external'] += re.findall(r'https?://[^\s)>\]]+', text)
                        elif s['type'] == 'quiz':
                            fn = base + '.md'; text = self.quiz(s, img_dir)
                        elif s['type'] == 'code':
                            fn = base + '.md'; text = self.code(s, img_dir)
                        elif s['type'] == 'video':
                            fn = base + '.md'; text = '# %s\n\nVideo: %s\n' % (s['title'], det.get('url') or det.get('file_url') or json.dumps(det)[:300])
                        else:
                            fn = base + '.md'; text = '# %s\n\n_Unsupported slide type "%s"; raw data below._\n\n```json\n%s\n```\n' % (s['title'], s['type'], json.dumps(det, indent=1)[:4000])
                        if text is not None:
                            open(os.path.join(ldir, fn), 'w', encoding='utf-8').write(text)
                        self.stats['written'] += 1
                    except Exception as e:
                        fn = base + ' (FAILED).md'
                        open(os.path.join(ldir, fn), 'w', encoding='utf-8').write('# %s\n\nFailed: %s\n' % (s['title'], e))
                        self.stats['failed'].append((l['title'], s['title'], str(e)))
                    lidx.append('- [%s](<%s>)' % (s['title'], fn))
                    index.append('- [%s](<%s/%s/%s>)' % (s['title'], safe(g['name']), safe(l['title']), fn))
                    made.update((fn, base + '.md'))
                kept = self.only is not None and g['name'] not in self.only
                if not kept:   # slides removed or renumbered on Ed: retire edpack's old numbered files
                    for f in sorted(os.listdir(ldir)):
                        if re.match(r'\d{2} - ', f) and f not in made and os.path.isfile(os.path.join(ldir, f)):
                            retire(self.out, os.path.join(ldir, f))
                if not (kept and os.path.exists(os.path.join(ldir, 'README.md'))):
                    open(os.path.join(ldir, 'README.md'), 'w', encoding='utf-8').write('\n'.join(lidx) + '\n')
        open(os.path.join(self.out, 'README.md'), 'w', encoding='utf-8').write('\n'.join(index) + '\n')
        json.dump(self.stats, open(os.path.join(self.raw, 'build_stats.json'), 'w'), indent=1)
        log('built %d/%d slides, %d failed -> %s' % (self.stats['written'], self.stats['slides'], len(self.stats['failed']), self.out))


def cmd_build(a):
    Builder(a.out, getattr(a, 'fresh', None)).build()


# ----------------------------------------------------------------------------- NotebookLM pack
def cmd_nblm(a):
    root = a.out; up = os.path.join(root, 'NotebookLM upload')
    # Everything here is generated from the week folders, so start clean: a renamed or removed
    # slide must not leave an old copy behind to be uploaded twice.
    if os.path.isdir(up): shutil.rmtree(up)
    os.makedirs(up)
    dump = json.load(open(os.path.join(root, '_raw', 'ed_dump.json'), encoding='utf-8'))
    npdf = nmd = nfig = 0
    for g in dump['modules']:
        gdir = os.path.join(root, safe(g['name']))
        wk = g.get('week') or (int(re.search(r'week\s*(\d+)', g['name'], re.I).group(1)) if re.search(r'week\s*(\d+)', g['name'], re.I) else None)
        tag = 'W%02d' % wk if wk else safe(g['name'])[:20]
        parts = ['# ' + g['name'], '', '_Merged text of all readings, notes, code challenges and quizzes. PDFs are uploaded separately._', '']
        figures = []
        for l in g['lessons']:
            ldir = os.path.join(gdir, safe(l['title']))
            if not os.path.isdir(ldir): continue
            parts += ['', '---', '', '# ' + l['title'], '']
            for fn in sorted(os.listdir(ldir)):
                p = os.path.join(ldir, fn)
                if fn.lower().endswith('.pdf'):
                    dst = '%s %s - %s' % (tag, safe(l['title']), fn.split(' - ', 1)[-1])
                    open(os.path.join(up, dst), 'wb').write(open(p, 'rb').read()); npdf += 1
                    parts += ['_(PDF uploaded separately: %s)_' % dst, '']
                    continue
                if not fn.endswith('.md') or fn == 'README.md' or fn[:-3] + '.pdf' in os.listdir(ldir): continue
                body = open(p, encoding='utf-8').read()
                body = re.sub(r'^<!-- source:.*?-->\n*', '', body)
                for m in re.finditer(r'!\[([^\]]*)\]\((images/[^)]+)\)', body):
                    hs = re.findall(r'^#{1,6} (.+)$', body[:m.start()], flags=re.M)
                    figures.append((l['title'], fn.split(' - ', 1)[-1][:-3], hs[-1].strip() if hs else '', m.group(1), os.path.join(ldir, m.group(2))))
                body = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', body)
                body = re.sub(r'^(#{1,5}) ', lambda m: '#' * (len(m.group(1)) + 1) + ' ', body, flags=re.M)
                parts += ['## ' + fn.split(' - ', 1)[-1][:-3], '', body.strip(), '']
        open(os.path.join(up, '%s - %s.md' % (tag, safe(g['name']).split(' - ', 1)[-1])), 'w', encoding='utf-8').write('\n'.join(parts) + '\n'); nmd += 1
        if figures:
            doc = pymupdf.open(); W, H, M = 595, 842, 40
            for i, (lesson, section, heading, alt, path) in enumerate(figures, 1):
                page = doc.new_page(width=W, height=H); y = M
                page.insert_text((M, y + 12), '%s  -  %s' % (g['name'], lesson), fontsize=9, color=(0.4, 0.4, 0.4)); y += 22
                page.insert_text((M, y + 14), 'Figure %d: %s' % (i, section), fontsize=13); y += 24
                if heading and heading != section:
                    page.insert_text((M, y + 11), 'Section: ' + heading, fontsize=10, color=(0.3, 0.3, 0.3)); y += 18
                y += 6
                try:
                    pix = pymupdf.Pixmap(path); s = min((W - 2 * M) / pix.width, (H - y - M - 90) / pix.height, 1.5)
                    rect = pymupdf.Rect(M, y, M + pix.width * s, y + pix.height * s); page.insert_image(rect, filename=path); y = rect.y1 + 14
                except Exception:
                    page.insert_text((M, y + 11), '[image could not be embedded: %s]' % os.path.basename(path), fontsize=10); y += 20
                page.insert_textbox(pymupdf.Rect(M, y, W - M, H - M), 'Caption / alt text: ' + (alt or '(none given)'), fontsize=10.5)
            doc.save(os.path.join(up, '%s - Figures and images.pdf' % tag)); doc.close(); nfig += 1
    log('NotebookLM upload folder: %d merged .md, %d PDFs, %d figure PDFs -> %s' % (nmd, npdf, nfig, up))


# ----------------------------------------------------------------------------- Moodle files
# Ed slides often just say "download the zip from here: <Moodle link>". Monash has the Moodle
# mobile web service turned off, so there is no API token; we borrow the browser's login
# session cookie instead. Downloads go into a "moodle" folder inside the lesson folder:
# they are part of the offline archive, not the NotebookLM upload.
MOODLE_LINK = re.compile(r'https?://[^\s)<>\]"\']+/mod/(?:resource|folder)/view\.php\?id=\d+')

class MoodleExpired(Exception): pass

class Moodle:
    def __init__(self, cookie, host):
        self.s = requests.Session(); self.s.headers['User-Agent'] = 'Mozilla/5.0 edpack/0.4'
        cookie = cookie.strip().strip('"')
        if cookie.lower().startswith('cookie:'): cookie = cookie[7:]
        if '=' not in cookie: cookie = 'MoodleSession=' + cookie
        for part in cookie.split(';'):   # set on the Moodle host only, never sent to Okta
            if '=' in part:
                k, v = part.split('=', 1); self.s.cookies.set(k.strip(), v.strip(), domain=host)

    def get(self, url):
        r = self.s.get(url, timeout=120)
        if 'okta.com' in r.url or '/login/' in urlparse(r.url).path:
            raise MoodleExpired()
        r.raise_for_status()
        return r

def _filename(r):
    cd = r.headers.get('Content-Disposition', '')
    m = re.search(r"filename\*=UTF-8''([^;]+)", cd, re.I) or re.search(r'filename="?([^";]+)"?', cd, re.I)
    return unquote(m.group(1) if m else os.path.basename(urlparse(r.url).path)) or 'file'

def moodle_files(mo, url):
    """(filename, bytes) for each file behind a Moodle resource or folder link."""
    r = mo.get(url)
    if 'text/html' not in r.headers.get('Content-Type', ''):
        return [(_filename(r), r.content)]          # Moodle sent the file straight away
    page = BeautifulSoup(r.text, 'html.parser')
    main = page.select_one('#region-main') or page
    hrefs = []
    for t in main.select('a[href*="pluginfile.php"], [src*="pluginfile.php"], object[data*="pluginfile.php"]'):
        h = t.get('href') or t.get('src') or t.get('data')
        if h and '/user/icon/' not in h and h not in hrefs: hrefs.append(h)
    if not hrefs:
        raise ValueError('no file found on the Moodle page')
    out = []
    for h in hrefs:
        f = mo.get(urljoin(r.url, h)); out.append((_filename(f), f.content))
    return out

def moodle_links(root, dump):
    """(url, lesson dir) for every Moodle file link in the archive's slides."""
    found = []
    for g in dump['modules']:
        for l in g['lessons']:
            ldir = os.path.join(root, safe(g['name']), safe(l['title']))
            if not os.path.isdir(ldir): continue
            for fn in sorted(os.listdir(ldir)):
                if fn.endswith('.md') and fn != 'README.md':
                    for u in MOODLE_LINK.findall(open(os.path.join(ldir, fn), encoding='utf-8').read()):
                        if (u, ldir) not in found: found.append((u, ldir))
    return found

def moodle_done(root):
    p = os.path.join(root, '_raw', 'moodle.json')
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {}

def moodle_key(root, url, ldir):
    return '%s | %s' % (os.path.relpath(ldir, root), url)

MOODLE_HELP = '''
  To download them, edpack needs your Moodle login session (it is never saved to disk):
    1. Open Moodle in your browser and make sure you are logged in.
    2. Press F12. Chrome/Edge: Application tab > Cookies; Firefox: Storage tab > Cookies.
    3. Click the Moodle site, find the row named MoodleSession, copy its Value.
'''

def cmd_moodle(a):
    root = a.out
    dump = json.load(open(os.path.join(root, '_raw', 'ed_dump.json'), encoding='utf-8'))
    done = moodle_done(root)
    links = moodle_links(root, dump)
    def have(u, d):
        k = moodle_key(root, u, d)
        return k in done and all(os.path.exists(os.path.join(d, 'moodle', f)) for f in done[k])
    todo = [(u, d) for u, d in links if not have(u, d)]
    # Weeks picked again this run (or every week, for a plain `edpack moodle`) also get their
    # already-downloaded files checked, in case the lecturer replaced one behind the same link.
    fresh = getattr(a, 'fresh', None)
    weeks = None if fresh is None else {safe(n) for n in fresh}
    recheck = [(u, d) for u, d in links if have(u, d) and (weeks is None or os.path.relpath(d, root).split(os.sep)[0] in weeks)]
    if not todo and not recheck:
        log('Moodle: %s' % ('no Moodle file links in these slides' if not links else 'all %d linked file(s) already downloaded' % len(links)))
        return
    what = ', '.join(x for x in ('%d new file link(s) to download' % len(todo) if todo else '',
                                 '%d downloaded file(s) to check for updates' % len(recheck) if recheck else '') if x)
    cookie = os.environ.get('MOODLE_SESSION')
    if not cookie:
        if not sys.stdin.isatty():
            log('Moodle: %s. Run  edpack moodle --out "%s"  to do it.' % (what, root)); return
        print('\n  Moodle: %s (zips, scripts, handouts).' % what + MOODLE_HELP)
        cookie = ask('  Paste the MoodleSession value (or press Enter to skip)')
        if not cookie:
            log('Moodle: skipped. Run  edpack moodle --out "%s"  any time.' % root); return
    mo = Moodle(cookie, urlparse((todo or recheck)[0][0]).netloc)
    got = updated = 0; failed = []
    jobs = todo + recheck
    for i, (u, d) in enumerate(jobs, 1):
        progress(i, len(jobs), os.path.relpath(d, root)[:45])
        k = moodle_key(root, u, d); mdir = os.path.join(d, 'moodle')
        try:
            files = [(safe(n) or 'file', b) for n, b in moodle_files(mo, u)]
            for name in done.get(k, []):            # file renamed or dropped on Moodle
                if name not in {n for n, _ in files} and os.path.exists(os.path.join(mdir, name)):
                    retire(root, os.path.join(mdir, name))
            for name, data in files:
                p = os.path.join(mdir, name)
                if os.path.exists(p):
                    if open(p, 'rb').read() == data: continue
                    retire(root, p); updated += 1   # lecturer replaced the file: keep the old copy aside
                else:
                    got += 1
                os.makedirs(mdir, exist_ok=True)
                open(p, 'wb').write(data)
            done[k] = [n for n, _ in files]
        except MoodleExpired:
            log('\nMoodle sent edpack to the login page, so that session value is wrong or has expired.'
                '\nLog in to Moodle again, copy a fresh MoodleSession value and run  edpack moodle --out "%s"' % root)
            if not (got or updated): return
            break
        except Exception as e:
            failed.append((u, str(e)))
    json.dump(done, open(os.path.join(root, '_raw', 'moodle.json'), 'w', encoding='utf-8'), indent=1)
    log('\nMoodle: %d new file(s), %d updated (%d link(s) re-checked), %d failed' % (got, updated, len(recheck), len(failed)))
    for u, e in failed: log('  ! %s: %s' % (u, e))


# ----------------------------------------------------------------------------- audit
def cmd_audit(a):
    root = a.out; raw = os.path.join(root, '_raw')
    dump = json.load(open(os.path.join(raw, 'ed_dump.json'), encoding='utf-8'))
    stats = json.load(open(os.path.join(raw, 'build_stats.json'))) if os.path.exists(os.path.join(raw, 'build_stats.json')) else {}
    problems = []
    expected = sum(len(l['slides']) for g in dump['modules'] for l in g['lessons'])
    files = pdfs = pdf_pages = pdf_blank = quiz_q = quiz_ok = quiz_none = imgs_missing = short = link_only = 0
    for g in dump['modules']:
        for l in g['lessons']:
            ldir = os.path.join(root, safe(g['name']), safe(l['title']))
            if not os.path.isdir(ldir):
                problems.append('missing lesson folder: ' + ldir); continue
            for fn in os.listdir(ldir):
                p = os.path.join(ldir, fn)
                if fn == 'README.md' or os.path.isdir(p): continue
                files += 1
                if 'FAILED' in fn: problems.append('failed slide: ' + p)
                if fn.endswith('.pdf'):
                    pdfs += 1; d = pymupdf.open(p); pdf_pages += len(d)
                    blank = sum(1 for pg in d if not pg.get_text('text').strip())
                    pdf_blank += blank
                    if blank == len(d): problems.append('PDF has no text on any page (scanned?): ' + p)
                elif fn.endswith('.md'):
                    t = open(p, encoding='utf-8').read()
                    if len(t.split()) < 30 and not fn[:-3] + '.pdf' in os.listdir(ldir):
                        if re.search(r'https?://', t) or 'interactive workspace only' in t:
                            link_only += 1   # slide is just a pointer (Moodle file, video); reported below
                        else:
                            short += 1; problems.append('very short file (%d words): %s' % (len(t.split()), p))
                    for m in re.finditer(r'!\[[^\]]*\]\((images/[^)]+)\)', t):
                        if not os.path.exists(os.path.join(ldir, m.group(1))):
                            imgs_missing += 1; problems.append('image link broken: %s -> %s' % (p, m.group(1)))
            for s in l['slides']:
                if s['type'] == 'quiz':
                    resp = {r['question_id']: r for r in s.get('responses', [])}
                    for q in s.get('questions', []):
                        quiz_q += 1
                        d = q['data']
                        if d.get('solution') not in (None, '', []) or (q['id'] in resp and resp[q['id']].get('correct')) \
                                or (d.get('type') in ('short-answer', 'general') and not d.get('assessed')):
                            quiz_ok += 1
                        elif q['id'] not in resp: quiz_none += 1
    low = [(u, pw, mw) for u, pw, mw in stats.get('webpages', []) if pw and mw / pw < 0.7]
    for u, pw, mw in low: problems.append('webpage lost text in conversion (%d -> %d words): %s' % (pw, mw, u))
    print('\n=== edpack audit ===')
    print('slides in Ed API      : %d' % expected)
    print('slides written        : %d   (failed: %d)' % (stats.get('written', 0), len(stats.get('failed', []))))
    print('files on disk         : %d' % files)
    print('PDFs                  : %d  (%d pages, %d without text)' % (pdfs, pdf_pages, pdf_blank))
    print('reading pages         : %d  (text kept, median ratio %.2f)' % (len(stats.get('webpages', [])), _median([mw / pw for _, pw, mw in stats.get('webpages', []) if pw]) if stats.get('webpages') else 0))
    print('quiz questions        : %d  (%d confirmed correct, %d unanswered)' % (quiz_q, quiz_ok, quiz_none))
    print('fetch errors          : %d' % len(dump.get('errors', [])))
    print('external links noted  : %d' % len(set(stats.get('external', []))))
    print('link-only slides      : %d  (Moodle files, videos, empty Ed workspaces; not counted as problems)' % link_only)
    links = moodle_links(root, dump); mdone = moodle_done(root)
    pending = [(u, d) for u, d in links if moodle_key(root, u, d) not in mdone]
    print('Moodle files          : %d of %d link(s) downloaded%s' % (len(links) - len(pending), len(links),
          ('   (get the rest:  edpack moodle --out "%s")' % root) if pending else ''))
    print('problems              : %d' % len(problems))
    for p in problems: print('  ! ' + p)
    if not problems: print('  none - archive is complete and consistent')

def _median(xs):
    xs = sorted(xs); n = len(xs)
    return 0 if not n else (xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2)


# ----------------------------------------------------------------------------- setup / run
def cmd_setup(a):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    cfg = load_config()
    print('Create a token at https://edstem.org/au/settings/api-tokens (change "au" to your region).')
    t = input('Paste your Ed API token: ').strip()
    if t: cfg['token'] = t
    json.dump(cfg, open(CONFIG, 'w', encoding='utf-8'))
    print('saved to', CONFIG)

def cmd_run(a):
    cmd_fetch(a); cmd_build(a); cmd_nblm(a); cmd_moodle(a); cmd_audit(a)


def ask(prompt, default=None):
    try:
        s = input('%s%s: ' % (prompt, (' [%s]' % default) if default is not None else '')).strip()
    except EOFError:   # no keyboard (piped or scheduled run): take the default
        s = ''
    return s or (default if default is not None else '')

def remember_location(course_id, path):
    cfg = load_config()
    cfg.setdefault('course_dirs', {})[str(course_id)] = path
    os.makedirs(CONFIG_DIR, exist_ok=True)
    json.dump(cfg, open(CONFIG, 'w', encoding='utf-8'))

def existing_dump(path):
    """The edpack archive data already in this folder, or None."""
    p = os.path.join(path, '_raw', 'ed_dump.json')
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return None

def choose_location(course, module_names):
    """Pick the course folder itself; Ed's week folders are created directly inside it."""
    home = os.path.expanduser('~')
    code = safe(course.get('code') or ('ed-%s' % course['id']))
    last = load_config().get('course_dirs', {}).get(str(course['id']))
    options = []
    if last and os.path.isdir(last):
        options.append(('last used for %s' % code, last))
    for d in ('Documents', 'Desktop', 'Downloads'):
        if os.path.isdir(os.path.join(home, d)):
            p = os.path.join(home, d, code)
            if all(os.path.normcase(p) != os.path.normcase(o) for _, o in options):
                options.append(('' if os.path.isdir(p) else 'new', p))
    print('\n  Which folder is this course\'s archive? The week folders go straight inside it.')
    for i, (tag, p) in enumerate(options, 1):
        print('   %2d. %s%s' % (i, p, ('   (%s)' % tag) if tag else ''))
    print('    or paste any folder path')
    while True:
        s = ask('  Choice', '1')
        if s.isdigit() and 1 <= int(s) <= len(options):
            p = options[int(s) - 1][1]
        else:
            p = os.path.abspath(os.path.expanduser(s.strip().strip('"')))
        old = existing_dump(p)
        if old and not same_course(old, course['id'], module_names):
            print('  That folder already holds an archive of a different Ed course (%s). Pick another.' % (old.get('course') or 'unknown')); continue
        if not os.path.isdir(p):
            drive = os.path.splitdrive(p)[0] + os.sep
            if not os.path.isdir(drive if os.path.splitdrive(p)[0] else os.path.dirname(p)):
                print('  That location was not found. Type a number from the list or a full path.'); continue
            if ask('  %s does not exist yet. Create it? (y/n)' % p, 'y').lower() not in ('y', 'yes'):
                continue
            os.makedirs(p)
        return p

def wizard():
    """Interactive mode: runs when edpack is started with no arguments."""
    print('\n  edpack  -  Ed Lessons -> offline folder + NotebookLM pack')
    print('  No AI involved. Everything is fetched from the Ed API and converted locally.\n')
    if not (os.environ.get('ED_TOKEN') or load_config().get('token')):
        print('  First time: you need an Ed API token.')
        cmd_setup(None); print()
    ed = Ed(token())
    print('  Loading your courses...')
    me = ed.get('/user')
    courses = [c['course'] for c in me.get('courses', [])]
    courses.sort(key=lambda c: (str(c.get('year', '')), str(c.get('session', '')), c.get('code', '')), reverse=True)
    if not courses:
        sys.exit('  No courses found on this Ed account.')
    last = None
    while True:
        last = archive_one(ed, courses)
        print('\n  What next?')
        print('    1. Archive another course or week')
        if last: print('    2. Open the last archive folder again')
        print('    q. Quit')
        while True:
            s = ask('  Choice', 'q').lower()
            if s in ('q', 'quit', 'exit'): print('  Bye.'); return
            if s == '1': break
            if s == '2' and last and sys.platform == 'win32': os.startfile(last); continue
            print('  Type 1, 2 or q.')

def archive_one(ed, courses):
    """One pass of the wizard: pick course, weeks, location; run; return the output folder."""
    print('\n  Which course?')
    for i, c in enumerate(courses, 1):
        print('   %2d. %s  %s  (%s %s)' % (i, c.get('code', ''), c.get('name', ''), c.get('year', ''), c.get('session', '')))
    while True:
        pick = ask('  Number', '1')
        if not (pick.isdigit() and 1 <= int(pick) <= len(courses)):
            print('  Please type a number from the list.'); continue
        course = courses[int(pick) - 1]
        L = ed.get('/courses/%s/lessons' % course['id'])
        if L.get('lessons'): break
        print('  %s has no Ed Lessons (it may only use Ed for discussion). Pick another course.' % course.get('code'))
    weeks_avail = sorted({w for mod in L.get('modules', []) for w in [week_of(mod['name'])] if w is not None})
    print('\n  %s has %d lessons in %d modules.' % (course.get('code'), len(L.get('lessons', [])), len(L.get('modules', []))))
    if weeks_avail:
        print('  Weeks available: %s' % ', '.join(str(w) for w in weeks_avail))
        while True:
            weeks = ask('  Which weeks? (e.g. 1  or  1-8  or  1,3,5  or  all)', 'all')
            try:
                err = check_weeks(parse_weeks(weeks), weeks_avail)
            except ValueError:
                err = '"%s" is not a week list. Type e.g. 9  or  1-8  or  1,3,5  or  all' % weeks
            if not err: break
            print('  ' + err)
        weeks = None if weeks.strip().lower() == 'all' else weeks
    else:
        print('  Modules are not named by week, so everything will be archived.'); weeks = None

    out = choose_location(course, [m['name'] for m in L.get('modules', [])])
    remember_location(course['id'], out)
    old = existing_dump(out)
    if old:
        print('\n  This folder already has: %s' % '; '.join(g['name'] for g in old['modules']))
        print('  New weeks are added; weeks you picked again are refreshed. Other weeks are left as they are.')
    mods = [m['name'] for m in L.get('modules', []) if weeks is None or week_of(m['name']) in parse_weeks(weeks)]
    print('\n  Will write into:\n    %s' % out)
    for m in mods: print('      %s' % safe(m))
    print()
    if ask('  Start? (y/n)', 'y').lower() not in ('y', 'yes'):
        print('  Cancelled.'); return None

    a = argparse.Namespace(course=str(course['id']), weeks=weeks, out=out)
    t0 = time.time()
    print('\n== 1/5 Downloading from Ed'); cmd_fetch(a)
    print('\n== 2/5 Converting to Markdown and collecting PDFs'); cmd_build(a)
    print('\n== 3/5 Building the NotebookLM upload folder'); cmd_nblm(a)
    print('\n== 4/5 Files linked on Moodle'); cmd_moodle(a)
    print('\n== 5/5 Checking the result'); cmd_audit(a)
    print('\n  Done in %d seconds.' % (time.time() - t0))
    print('  Archive:            %s' % out)
    print('  NotebookLM upload:  %s' % os.path.join(out, 'NotebookLM upload'))
    print('  Drag everything in that upload folder into NotebookLM > Add sources > Upload files.\n')
    if sys.platform == 'win32' and ask('  Open the folder now? (y/n)', 'y').lower() in ('y', 'yes'):
        os.startfile(out)
    return out


def main():
    if len(sys.argv) == 1:
        try:
            wizard()
        except KeyboardInterrupt:
            print('\n  Cancelled.')
        return
    p = argparse.ArgumentParser(prog='edpack', description='Archive Ed Lessons offline, NotebookLM-ready. No AI involved. Run with no arguments for the interactive mode.')
    sub = p.add_subparsers(dest='cmd', required=True)
    for name, fn, needs in [('setup', cmd_setup, False), ('fetch', cmd_fetch, True), ('build', cmd_build, True),
                            ('nblm', cmd_nblm, True), ('moodle', cmd_moodle, True), ('audit', cmd_audit, True), ('run', cmd_run, True)]:
        sp = sub.add_parser(name); sp.set_defaults(fn=fn)
        if needs:
            sp.add_argument('--course', required=name in ('fetch', 'run'), help='Ed course id, e.g. 20603')
            sp.add_argument('--weeks', help='e.g. 1  or  1-8  or  1,3,5   (default: all)')
            sp.add_argument('--out', help='output folder (default: ./ed-<course>)')
    a = p.parse_args()
    if getattr(a, 'course', None) is None and getattr(a, 'out', None):
        pass
    if hasattr(a, 'out') and not a.out:
        a.out = os.path.abspath('ed-%s' % a.course) if getattr(a, 'course', None) else sys.exit('--out or --course required')
    a.fn(a)

if __name__ == '__main__':
    main()
