"""Render the profile in the reference layout; update uptime and public GitHub stats."""
from pathlib import Path
from datetime import date, datetime
from zoneinfo import ZoneInfo
import calendar, json, os, subprocess, tempfile, time, urllib.request, urllib.error
from urllib.parse import urlencode
from html import escape

ROOT = Path(__file__).resolve().parents[1]

def uptime(birth, today):
    years = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
    anniversary = birth.replace(year=birth.year + years)
    months = (today.year - anniversary.year) * 12 + today.month - anniversary.month

    def advance(n):
        total = anniversary.year * 12 + anniversary.month - 1 + n
        y, m = divmod(total, 12)
        return date(y, m + 1, min(anniversary.day, calendar.monthrange(y, m + 1)[1]))

    if advance(months) > today:
        months -= 1

    days = (today - advance(months)).days
    return f'{years} years, {months} months, {days} days'


def fetch_stats(login):
    token = os.environ.get('GH_TOKEN', '')

    def api(path):
        headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'profile-readme'}
        if token:
            headers['Authorization'] = f'token {token}'
        last_error = None
        for attempt in range(3):
            try:
                request = urllib.request.Request('https://api.github.com' + path, headers=headers)
                with urllib.request.urlopen(request, timeout=30) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                body = error.read().decode('utf-8', 'replace')
                try:
                    payload = json.loads(body)
                    message = payload.get('message', body)
                except json.JSONDecodeError:
                    message = body or str(error)
                last_error = RuntimeError(f'GitHub API request failed for {path}: {error.code} {message}')
                if error.code in (403, 429) and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise last_error
            except (urllib.error.URLError, OSError) as error:
                last_error = RuntimeError(f'GitHub API request failed for {path}: {error}')
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise last_error
        if last_error is not None:
            raise last_error
        raise RuntimeError(f'GitHub API request failed for {path}: unknown error')

    user = api(f'/users/{login}')
    repos = []
    page = 1
    while True:
        batch = api(f'/users/{login}/repos?type=owner&per_page=100&page={page}')
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    stats = {
        'repos': user['public_repos'],
        'stars': sum(r['stargazers_count'] for r in repos),
        'followers': user['followers'],
    }

    # Public indexed commits and pull requests identify external repositories.
    external = set()
    for kind, query in [('commits', f'author:{login}'), ('issues', f'author:{login} type:pr')]:
        for page in range(1, 11):
            found = api('/search/' + kind + '?' + urlencode({'q': query, 'per_page': 100, 'page': page}))
            for item in found['items']:
                repo = item['repository']['full_name'] if kind == 'commits' else '/'.join(item['repository_url'].split('/')[-2:])
                if repo.split('/')[0].lower() != login.lower():
                    external.add(repo)
            if page * 100 >= min(found['total_count'], 1000):
                break

    stats['contributed'] = len(external)
    commits = adds = dels = 0

    with tempfile.TemporaryDirectory() as tmp:
        for i, repo in enumerate(repos):
            if repo['fork'] or repo['size'] == 0:
                continue
            dest = Path(tmp) / str(i)
            subprocess.run(['git', 'clone', '--quiet', '--bare', repo['clone_url'], str(dest)], check=True, capture_output=True, timeout=180)
            commits += int(subprocess.check_output(['git', '-C', str(dest), 'rev-list', '--count', 'HEAD'], text=True))
            output = subprocess.check_output(['git', '-C', str(dest), 'log', '--format=', '--numstat', '--no-merges', 'HEAD'], text=True)
            for line in output.splitlines():
                cols = line.split('\t')
                if len(cols) >= 3 and cols[0].isdigit() and cols[1].isdigit():
                    adds += int(cols[0])
                    dels += int(cols[1])

    stats.update(commits=commits, additions=adds, deletions=dels, lines=adds - dels)
    return stats


def cfg_value(cfg, key, default='—'):
    value = cfg.get(key)
    return value if value not in (None, '') else default


def render(mode, cfg, stats, age):
    palette = {
        'dark': [
            '#0d1117', '#30363d', '#8b949e', '#58a6ff',
            '#484f58', '#ffa657', '#c9d1d9', '#3fb950', '#f85149'
        ],
        'light': [
            '#ffffff', '#d0d7de', '#57606a', '#0969da',
            '#afb8c1', '#953800', '#24292f', '#3fb950', '#f85149'
        ],
    }

    bg, border, muted, blue, dots, orange, fg, green, red = palette[mode]

    out = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="840" height="500" viewBox="0 0 840 500" font-family="Consolas, Menlo, monospace" font-size="13px">',
        f'<title>{escape(str(cfg.get("name", "Profile")))} - GitHub Stats</title>',
        f'<rect width="840" height="500" fill="{bg}"/>',
    ]

    portrait = ROOT / 'portrait.txt'
    if portrait.exists():
        for i, line in enumerate(portrait.read_text().splitlines()[:48]):
            out.append(f'<text x="16" y="{52 + i * 10}" font-size="9px" fill="{muted}" xml:space="preserve">{escape(line[:68])}</text>')
    else:
        art = [
            '██╗  ██╗███████╗',
            '██║  ██║██╔════╝',
            '███████║█████╗  ',
            '██╔══██║██╔══╝  ',
            '██║  ██║███████╗',
            '╚═╝  ╚═╝╚══════╝',
        ]
        for i, line in enumerate(art):
            out.append(f'<text x="57" y="{177 + i * 24}" font-size="24px" fill="{muted}" xml:space="preserve">{line}</text>')

    def spans(y, items):
        out.append(
            f'<text x="390" y="{y}" xml:space="preserve">' +
            ''.join(f'<tspan fill="{color}">{escape(str(value))}</tspan>' for color, value in items) +
            '</text>'
        )

    def row(y, label, value):
        value = value or '—'
        count = max(2, 55 - len(label) - len(str(value)) - 4)
        spans(y, [(orange, label + ': '), (dots, '.' * count + ' '), (fg, value)])

    def section(y, title):
        spans(y, [(blue, title + ' '), (dots, '─' * max(2, 55 - len(title) - 1))])

    section(45, str(cfg_value(cfg, 'login', 'unknown')).lower() + '@github')
    row(87, 'OS', cfg_value(cfg, 'os'))
    row(108, 'Uptime', age)
    row(129, 'Host', cfg_value(cfg, 'host'))
    row(150, 'Kernel', cfg_value(cfg, 'kernel'))
    row(171, 'IDE', cfg_value(cfg, 'ide'))

    row(213, 'Languages.Programming', cfg_value(cfg, 'programming'))
    row(234, 'Languages.Real', cfg_value(cfg, 'languages'))
    row(255, 'Hobbies', cfg_value(cfg, 'hobbies'))

    section(297, '─ Contact')
    row(318, 'Email', cfg_value(cfg, 'email'))
    row(339, 'LinkedIn', cfg_value(cfg, 'linkedin'))

    section(381, '─ GitHub Stats')

    def n(key):
        return f'{stats[key]:,}' if key in stats else '—'

    spans(402, [
        (orange, 'Repos: '),
        (fg, n('repos') + ' {Contributed: ' + n('contributed') + '}'),
        (dots, ' | '),
        (orange, 'Stars: '),
        (dots, '.. '),
        (fg, n('stars'))
    ])
    spans(423, [
        (orange, 'Commits: '),
        (dots, '..... '),
        (fg, n('commits')),
        (dots, ' | '),
        (orange, 'Followers: '),
        (dots, '... '),
        (fg, n('followers'))
    ])
    spans(444, [
        (orange, 'Lines of Code: '),
        (fg, n('lines')),
        (dots, ' ( '),
        (green, n('additions') + '++'),
        (dots, ', '),
        (red, n('deletions') + '--'),
        (dots, ' )')
    ])

    out.append('</svg>')
    return '\n'.join(out) + '\n'


def main():
    cfg = json.loads((ROOT / 'profile.json').read_text())
    stats_path = ROOT / 'stats.json'
    stats = json.loads(stats_path.read_text()) if stats_path.exists() else {}

    if '--fetch' in __import__('sys').argv:
        try:
            stats = fetch_stats(cfg['login'])
        except Exception as error:
            print(f'::warning::Could not refresh public GitHub stats: {type(error).__name__}: {error}')
            if stats_path.exists():
                try:
                    stats = json.loads(stats_path.read_text())
                except Exception:
                    stats = {}
            else:
                stats = {}

    stats_path.write_text(json.dumps(stats, indent=2) + '\n')

    today = datetime.now(ZoneInfo('America/Sao_Paulo')).date()
    age = uptime(date.fromisoformat(cfg['birthdate']), today)
    for mode in ['dark', 'light']:
        (ROOT / f'{mode}_mode.svg').write_text(render(mode, cfg, stats, age))
    print(age)


if __name__ == '__main__':
    main()
