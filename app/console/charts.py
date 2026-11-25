"""Small server-rendered SVG line charts (mean with a +-1 SD bar per point).

Drawn on the server so the console page loads no chart library and costs almost no
CPU on CPU 0, which it shares with k6 while a run is measured.
"""

from html import escape

COLORS = {'ar': '#2563eb', 'repo': '#dc2626', 'qb': '#6b7280'}
NAMES = {'ar': 'Active Record', 'repo': 'Repository', 'qb': 'Query Builder'}

WIDTH, HEIGHT = 560, 280
LEFT, RIGHT, TOP, BOTTOM = 64, 16, 30, 44


def _nice_max(value: float) -> float:
    if value <= 0:
        return 1.0
    magnitude = 10 ** (len(str(int(value))) - 1)
    for step in (1, 2, 2.5, 5, 10):
        if value <= step * magnitude:
            return step * magnitude
    return 10 * magnitude


def _number(value: float) -> str:
    return f'{value:,.0f}' if value >= 100 else f'{value:,.1f}'.rstrip('0').rstrip('.')


def line_chart(series: dict[str, list[tuple[float, float, float | None]]], x_label: str, y_label: str) -> str:
    """series: implementation -> [(x, mean, sd)]"""
    points = [p for values in series.values() for p in values if p[1] is not None]
    if not points:
        return '<p class="muted">Belum ada run valid.</p>'

    xs = sorted({p[0] for p in points})
    y_max = _nice_max(max(p[1] + (p[2] or 0) for p in points))
    plot_w, plot_h = WIDTH - LEFT - RIGHT, HEIGHT - TOP - BOTTOM

    def sx(x):
        if len(xs) == 1:
            return LEFT + plot_w / 2
        return LEFT + (x - xs[0]) / (xs[-1] - xs[0]) * plot_w

    def sy(y):
        return TOP + plot_h - y / y_max * plot_h

    parts = [f'<svg viewBox="0 0 {WIDTH} {HEIGHT}" class="chart" role="img" '
             f'aria-label="{escape(y_label)} terhadap {escape(x_label)}">']
    for i in range(5):
        y = y_max * i / 4
        parts.append(f'<line x1="{LEFT}" x2="{WIDTH - RIGHT}" y1="{sy(y):.1f}" y2="{sy(y):.1f}" class="grid"/>')
        parts.append(f'<text x="{LEFT - 8}" y="{sy(y) + 4:.1f}" text-anchor="end" class="tick">{_number(y)}</text>')
    for x in xs:
        parts.append(f'<text x="{sx(x):.1f}" y="{HEIGHT - BOTTOM + 18}" text-anchor="middle" class="tick">{_number(x)}</text>')
    parts.append(f'<text x="{LEFT + plot_w / 2:.1f}" y="{HEIGHT - 6}" text-anchor="middle" class="axis">{escape(x_label)}</text>')
    parts.append(f'<text x="14" y="{TOP + plot_h / 2:.1f}" text-anchor="middle" class="axis" '
                 f'transform="rotate(-90 14 {TOP + plot_h / 2:.1f})">{escape(y_label)}</text>')

    legend_x = LEFT + 8
    for impl, values in series.items():
        color = COLORS.get(impl, '#111827')
        values = sorted((p for p in values if p[1] is not None), key=lambda p: p[0])
        if not values:
            continue
        line = ' '.join(f'{sx(x):.1f},{sy(y):.1f}' for x, y, _ in values)
        parts.append(f'<polyline points="{line}" fill="none" stroke="{color}" stroke-width="2"/>')
        for x, y, sd in values:
            if sd:
                parts.append(f'<line x1="{sx(x):.1f}" x2="{sx(x):.1f}" y1="{sy(max(0, y - sd)):.1f}" '
                             f'y2="{sy(y + sd):.1f}" stroke="{color}" stroke-width="1.5"/>')
            parts.append(f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="3.5" fill="{color}">'
                         f'<title>{escape(NAMES.get(impl, impl))}: {_number(y)}'
                         + (f' ± {_number(sd)}' if sd else '') + '</title></circle>')
        parts.append(f'<rect x="{legend_x}" y="4" width="10" height="10" fill="{color}"/>')
        parts.append(f'<text x="{legend_x + 14}" y="13" class="legend">{escape(NAMES.get(impl, impl))}</text>')
        legend_x += 14 + 8 * len(NAMES.get(impl, impl)) + 16
    parts.append('</svg>')
    return ''.join(parts)
