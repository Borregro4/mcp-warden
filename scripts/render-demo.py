"""Render verified demo events as a terminal-style replay; never captures the desktop.

Optional authoring dependency: Pillow. Not imported by MCP Warden's runtime.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import PIL
from PIL import Image, ImageDraw, ImageFont


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True, help='Fresh directory for GIF, poster and render metadata')
    parser.add_argument('--font', type=Path, required=True, help='Local monospace TrueType font; not copied')
    args = parser.parse_args()
    evidence, out = args.evidence.resolve(), args.out.resolve()
    if out.exists():
        parser.error('Output directory must be new; previous renders are preserved')
    plan = json.loads((evidence / 'plan.json').read_text(encoding='utf-8'))
    result = json.loads((evidence / 'result.json').read_text(encoding='utf-8'))
    records = [json.loads(line) for line in (evidence / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
    rows = {row['event']: row for row in records}
    if not result['passed'] or len(result['checks']) != 9 or not all(result['checks'].values()):
        parser.error('Render only a complete passing demo')
    before, protected, completed = (rows[key] for key in ('echo_before', 'long_rpc_protected', 'long_rpc_completed'))
    idle, wake, cleanup = (rows[key] for key in ('idle_expired', 'echo_after_wake', 'cleanup'))
    proxy, old, new = before['proxy'], before['backend'], wake['backend']
    stamp = lambda row: f"[+{row['elapsedSeconds']:05.2f}s]"
    mib = lambda row: row['memory']['workingSetBytes'] / 1024**2
    scenes = [
        ('Connect once', 4000, [
            ('command', '$ node scripts/demo.mjs --out <fresh folder>'),
            ('muted', '  isolated state | stateless example | idle = 2 s'),
            ('normal', f"{stamp(rows['sdk_connected'])} MCP SDK {plan['runtime']['sdk']} connected"),
            ('normal', f"  tools/list -> {', '.join(rows['sdk_connected']['tools'])}"),
            ('green', f"  proxy PID {proxy['pid']} | one SDK connection"),
        ]),
        ('Check the first response', 4000, [
            ('command', '$ client.callTool({ name: "echo", ... })'),
            ('normal', f"{stamp(before)} echo returned the exact nonce"),
            ('normal', f"  {before['valueLabel']}"),
            ('green', f"  backend PID {old['pid']}"),
            ('muted', f"  Windows creation FILETIME: {old['created']}"),
            ('normal', f"  sampled working set: {mib(before):.2f} MiB (2 processes)"),
        ]),
        ('Protect work in progress', 5000, [
            ('command', '$ client.callTool({ name: "sleep", arguments: { seconds: 4 } })'),
            ('normal', f"{stamp(protected)} still pending after {protected['sinceRequestSeconds']:.3f} s"),
            ('normal', f"  idle limit: {plan['idleSeconds']} s | requested work: {plan['longRpcSeconds']} s"),
            ('green', f"  same backend PID {old['pid']} + creation time is alive"),
            ('green', '  pending RPC protected past the idle deadline'),
        ]),
        ('Complete the pending call', 4000, [
            ('normal', f"{stamp(completed)} sleep completed in {completed['durationSeconds']:.3f} s"),
            ('normal', f"  {completed['valueLabel']}"),
            ('green', f"  exact value | same backend PID {old['pid']}"),
            ('muted', '  Now stop sending requests and let the backend go idle.'),
        ]),
        ('Release the idle backend', 5000, [
            ('normal', f"{stamp(idle)} backend absent after {idle['sinceLastResponseSeconds']:.3f} s idle"),
            ('green', f"  PID {old['pid']} + creation time: gone"),
            ('green', f"  proxy PID {proxy['pid']} + creation time: alive"),
            ('normal', '  CLI status: 1 session | backendPid: null'),
            ('normal', f"  sampled working set: {mib(idle):.2f} MiB (proxy only)"),
            ('muted', '  Scope: proxy + direct backend; shared pages may overlap.'),
        ]),
        ('Wake through the same connection', 5000, [
            ('command', '$ client.callTool({ name: "echo", ... })'),
            ('normal', f"{stamp(wake)} reply in {wake['wakeSeconds'] * 1000:.1f} ms"),
            ('normal', f"  {wake['valueLabel']}"),
            ('green', f"  new backend PID {new['pid']} | same proxy PID {proxy['pid']}"),
            ('muted', f"  new creation FILETIME: {new['created']}"),
            ('green', '  exact nonce returned | SDK connection count: 1'),
        ]),
        ('Close and verify cleanup', 4000, [
            ('command', '$ await client.close()'),
            ('normal', f"{stamp(cleanup)} CLI status: 0 sessions"),
            ('green', '  remaining owned PID + creation identities: 0'),
            ('green', f"  cooperative proxy exit: {cleanup['proxyExitCode']} | stderr bytes: {cleanup['stderrBytes']}"),
            ('muted', '  No model calls. No existing client configuration changed.'),
        ]),
        ('9 / 9 checks passed', 9000, [
            ('green', '  PASS  isolated init        PASS  handshake + catalog'),
            ('green', '  PASS  exact echo           PASS  pending RPC protection'),
            ('green', '  PASS  clean idle expiry    PASS  same SDK connection'),
            ('green', '  PASS  new backend         PASS  explicit memory scope'),
            ('green', '  PASS  cleanup: zero owned identities left alive'),
            ('normal', f"  measured run: {result['elapsedSeconds']:.2f} s | backend idle: 2 s"),
            ('muted', f"  source: {result['sourceCommit'][:12]} | MCP SDK {plan['runtime']['sdk']}"),
            ('muted', '  Synthetic lifecycle proof; native app tests are separate.'),
        ]),
    ]
    out.mkdir(parents=True)
    width, height = 900, 570
    font = ImageFont.truetype(str(args.font), 18)
    small = ImageFont.truetype(str(args.font), 15)
    heading = ImageFont.truetype(str(args.font), 28)
    title = ImageFont.truetype(str(args.font), 24)
    colors = {'normal': '#d7e2ef', 'command': '#93c5fd', 'muted': '#9cacc1', 'green': '#82e8b0'}
    images, durations = [], []
    for index, (label, duration, lines) in enumerate(scenes):
        frame = Image.new('RGB', (width, height), '#0b1220')
        draw = ImageDraw.Draw(frame)
        draw.text((30, 20), 'MCP Warden', font=heading, fill='#f1f5f9')
        draw.text((30, 61), plan['scenario'], font=font, fill='#93c5fd')
        draw.text((30, 89), 'REAL RUN LOG  /  REPLAY SLOWED FOR READING', font=small, fill='#9cacc1')
        draw.rounded_rectangle((22, 124, 878, 500), radius=12, fill='#111e31', outline='#2a3c55', width=1)
        draw.text((43, 143), f'{index + 1:02d}  {label}', font=title, fill='#f1f5f9')
        draw.line((43, 182, 857, 182), fill='#2a3c55')
        for number, (kind, line) in enumerate(lines):
            if draw.textlength(line, font=font) > 814:
                raise ValueError('Text would overflow; shorten this scene before publishing')
            draw.text((43, 200 + 32 * number), line, font=font, fill=colors[kind])
        draw.text((30, 520), 'Python stdio proxy -> bundled echo backend | no model calls', font=small, fill='#9cacc1')
        draw.text((30, 545), 'Measured events + reproduction: docs/demo.md', font=small, fill='#9cacc1')
        for mark in range(len(scenes)):
            draw.rounded_rectangle((712 + mark * 20, 531, 723 + mark * 20, 537), radius=2,
                                   fill='#82e8b0' if mark <= index else '#2a3c55')
        images.append(frame)
        durations.append(duration)
    images[-1].save(out / 'demo-poster.png')
    images[0].save(out / 'demo.gif', save_all=True, append_images=images[1:],
                   duration=durations, loop=0, optimize=True, disposal=2)
    # Verify the actual encoded asset, not just the requested options.
    with Image.open(out / 'demo.gif') as actual:
        actual_duration = sum(actual.seek(i) or actual.info['duration'] for i in range(actual.n_frames))
        metadata = {'renderer': 'Pillow', 'version': PIL.__version__, 'rendererSha256': sha256(Path(__file__)),
            'dimensions': list(actual.size),
            'frames': actual.n_frames, 'durationMs': actual_duration, 'loop': actual.info.get('loop'),
            'lastFrameMs': durations[-1], 'inputSha256': {name: sha256(evidence / name)
                for name in ('plan.json', 'events.jsonl', 'result.json')},
            'outputSha256': {name: sha256(out / name) for name in ('demo.gif', 'demo-poster.png')},
            'fontFamily': 'local monospace font; not bundled',
            'scope': 'Editorial terminal replay of actual verified observations; no desktop capture.'}
    assert 30000 <= actual_duration <= 45000 and actual.n_frames == len(scenes)
    (out / 'render.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(metadata))


if __name__ == '__main__':
    main()
