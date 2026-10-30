"""Opt-in Claude Code integration check using the user's existing login.

Runs only the synthetic example MCP. Does not install clients or edit settings.
Raw output and local state belong in a private directory outside the repository.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def persistent_turns(executable, model, cwd, output, timeout, health, environment, mcp_config):
    """Use the official SDK for two native turns; do not handcraft its protocol."""
    try:
        from claude_agent_sdk import (
            AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient,
            ResultMessage, SystemMessage, UserMessage,
        )
    except ImportError as error:
        raise RuntimeError('reconnect scenario requires claude-agent-sdk==0.2.175 in an isolated environment') from error
    messages, errors = [], []
    prompts = [
        'Call mcp__warden-example__echo once with value "native-before-expiry" and report the returned value and PID. Use no other tool.',
        'Now call mcp__warden-example__echo with value "native-after-expiry". Report the actual result or error. Do not substitute a remembered value. Use no other tool.',
    ]
    report = {'first_result': None, 'second_result': None, 'expiry_observed': False,
              'control_initialized': False, 'startup_reconnects': 0, 'driver': 'claude-agent-sdk==0.2.175'}
    options = ClaudeAgentOptions(
        cli_path=str(executable), cwd=str(cwd), model=model, effort='low',
        mcp_servers=mcp_config['mcpServers'], strict_mcp_config=True,
        tools=[], allowed_tools=['mcp__warden-example__echo'],
        permission_mode='dontAsk', setting_sources=[], max_turns=4,
        system_prompt='You validate a synthetic local MCP test. Follow the user prompt exactly. Do not delegate.',
        env={'MCP_TIMEOUT': environment['MCP_TIMEOUT']},
        stderr=lambda line: errors.append(line + '\n'),
        extra_args={'restricted': None, 'disable-slash-commands': None,
                    'no-session-persistence': None, 'permission-prompts': 'none',
                    'debug': 'mcp', 'debug-file': str(output / 'claude-mcp-debug.log')},
    )

    async def run():
        async with ClaudeSDKClient(options=options) as client:
            report['control_initialized'] = True
            status = await client.get_mcp_status()
            servers = status.get('mcpServers', [])
            report['initial_status'] = [{'name': server['name'], 'status': server['status']}
                                       for server in servers if server.get('name') == 'warden-example']
            deadline = time.monotonic() + 90
            while not any(server.get('name') == 'warden-example' and server.get('status') == 'connected' for server in servers):
                if any(server.get('name') == 'warden-example' and server.get('status') == 'failed' for server in servers):
                    raise RuntimeError('native MCP startup failed before inference')
                if time.monotonic() >= deadline:
                    raise RuntimeError('native MCP startup remained pending')
                await asyncio.sleep(.5)
                status = await client.get_mcp_status()
                servers = status.get('mcpServers', [])
            report['ready_status'] = 'connected'
            for index, prompt in enumerate(prompts):
                await client.query(prompt)
                async with asyncio.timeout(timeout):
                    async for message in client.receive_response():
                        data = dataclasses.asdict(message)
                        if isinstance(message, (AssistantMessage, UserMessage)):
                            kind = 'assistant' if isinstance(message, AssistantMessage) else 'user'
                            blocks = []
                            for block in message.content if isinstance(message.content, list) else []:
                                entry = dataclasses.asdict(block)
                                entry['type'] = {'ToolUseBlock': 'tool_use', 'ToolResultBlock': 'tool_result',
                                                 'TextBlock': 'text', 'ThinkingBlock': 'thinking'}.get(type(block).__name__, type(block).__name__)
                                blocks.append(entry)
                            data = {'type': kind, 'message': {'role': kind, 'content': blocks}}
                        elif isinstance(message, ResultMessage):
                            data['type'] = 'result'
                            report['first_result' if index == 0 else 'second_result'] = message.subtype
                        elif isinstance(message, SystemMessage):
                            data['type'] = 'system'
                        else:
                            data['type'] = type(message).__name__
                        messages.append(data)
                if index == 0:
                    deadline = time.monotonic() + 75
                    while time.monotonic() < deadline:
                        observed = health()
                        idle_stdio = observed.get('transport') == 'stdio' and observed.get('samples') \
                            and all(sample.get('backendPid') is None for sample in observed['samples'])
                        if observed.get('idleExpiredSessionCount', 0) >= 1 or idle_stdio:
                            report['expiry_observed'] = True
                            if idle_stdio:
                                report['stdio_owner_before'] = observed['samples'][0]['ownerPid']
                            break
                        await asyncio.sleep(.2)
                    else:
                        raise RuntimeError('gateway did not expire the idle native session')
                elif health().get('transport') == 'stdio':
                    remaining = health().get('samples', [])
                    report['stdio_owner_after'] = remaining[0].get('ownerPid') if remaining else None
    try:
        asyncio.run(run())
    finally:
        stdout = ('\n'.join(json.dumps(message, default=str) for message in messages) + '\n').encode('utf-8')
        stderr = ''.join(errors).encode('utf-8')
        (output / 'reconnect-observations.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        (output / 'claude-output.jsonl').write_bytes(stdout)
        (output / 'claude-stderr.log').write_bytes(stderr)
    return 0, stdout, stderr, report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--claude', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--model', default='sonnet')
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--scenario', choices=['basic', 'reconnect'], default='basic')
    parser.add_argument('--startup-timeout', type=int, default=90)
    parser.add_argument('--trace-connector', action='store_true', help='private stderr/startup diagnostics only')
    parser.add_argument('--transport', choices=['gateway', 'stdio'], default='gateway')
    args = parser.parse_args()
    executable = args.claude.resolve(strict=True)
    output = args.output_dir.resolve()
    if output == ROOT or ROOT in output.parents:
        parser.error('output-dir must be outside the repository')
    output.mkdir(parents=True, exist_ok=False)
    state, cwd = output / 'state', output / 'workspace'
    cwd.mkdir()
    with socket.socket() as reserve:
        reserve.bind(('127.0.0.1', 0))
        port = reserve.getsockname()[1]
    subprocess.run([sys.executable, '-I', '-B', str(ROOT / 'warden.py'), 'init',
                    '--state', str(state), '--port', str(port), '--transport', args.transport,
                    '--restart-policy', 'stateless' if args.transport == 'stdio' else 'manual',
                    '--idle-seconds', '120' if args.scenario == 'basic' else '3' if args.transport == 'stdio' else '60'],
                   check=True, stdout=subprocess.DEVNULL, timeout=15)
    config = json.loads((state / 'config.json').read_text(encoding='utf-8'))
    token = (state / 'token').read_text(encoding='ascii').strip()
    entry = subprocess.check_output([sys.executable, '-I', '-B', str(ROOT / 'warden.py'),
        'configuration', '--state', str(state), '--profile', 'example', '--client', 'claude'], text=True, timeout=15)
    if args.trace_connector:
        config_entry = json.loads(entry)
        backend_entry = config_entry['mcpServers']['warden-example']
        trace = output / 'trace_connector.py'
        preload = output / 'trace_node.cjs'
        preload.write_text(
            'const fs=require("fs");const output=' + json.dumps(str(output / 'node-lifecycle.jsonl')) + ';\n'
            'function note(event,code,extra={}){fs.appendFileSync(output,JSON.stringify({event,code,pid:process.pid,main:process.argv[1],cwd:process.cwd(),stdinEnded:process.stdin.readableEnded,stdinFlowing:process.stdin.readableFlowing,...extra})+"\\n");}\n'
            'const originalFetch=globalThis.fetch;globalThis.fetch=async(...args)=>{const headers=new Headers(args[1]?.headers);let shape={};try{const body=JSON.parse(args[1]?.body);shape={method:body.method,idType:typeof body.id,rpc:body.jsonrpc,paramsKeys:Object.keys(body.params??{}),clientInfoKeys:Object.keys(body.params?.clientInfo??{})};}catch{}note("fetch_start",null,{headersBytes:[...headers].reduce((n,[k,v])=>n+k.length+v.length+4,0),shape});try{const response=await originalFetch(...args);const known=new Set(["Invalid owner context","Initialize required","Invalid request body","Transport rejected request"]);const text=response.ok?"":await response.clone().text();note("fetch_response",response.status,{reason:known.has(text)?text:"other"});return response;}catch(error){note("fetch_error",null,{errorName:error.name,causeCode:error.cause?.code});throw error;}};\n'
            'note("start");process.stdin.on("end",()=>note("stdin_end"));process.stdin.on("close",()=>note("stdin_close"));\n'
            'process.on("beforeExit",code=>note("before_exit",code));process.on("exit",code=>note("exit",code));\n', encoding='utf-8')
        trace.write_text(
            'import subprocess,sys,pathlib,json,shutil,os\n'
            'folder=pathlib.Path(' + repr(str(output)) + ')\n'
            '(folder/"connector-start.json").write_text(json.dumps({"pid":os.getpid(),"node_on_path":bool(shutil.which("node"))}))\n'
            'env=os.environ.copy();env["NODE_OPTIONS"]="--require="+' + repr(str(preload)) + '\n'
            'with (folder/"connector-stderr.log").open("wb") as stderr:\n'
            '    child=subprocess.Popen(' + repr([backend_entry['command'], *backend_entry['args']]) + ',stdin=sys.stdin,stdout=sys.stdout,stderr=stderr,creationflags=0x08000000,env=env)\n'
            '    status=child.wait()\n'
            '(folder/"connector-exit.json").write_text(json.dumps({"exit":status}))\n'
            'sys.exit(status)\n', encoding='utf-8')
        config_entry['mcpServers']['warden-example'] = {'command': sys.executable, 'args': ['-I', '-B', '-u', str(trace)]}
        entry = json.dumps(config_entry)
    mcp = output / 'mcp.json'
    mcp.write_text(entry, encoding='utf-8')
    log = (output / 'gateway.log').open('wb')
    gateway = None
    if args.transport == 'gateway':
        gateway = subprocess.Popen([shutil.which('node'), str(ROOT / 'runtime' / 'gateway.mjs'),
            '--config', str(state / 'config.json'), '--stdin-control'],
            stdin=subprocess.PIPE, stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    client = None
    try:
        deadline = time.monotonic() + 60
        ready = gateway is None
        while gateway and time.monotonic() < deadline:
            if gateway.poll() is not None:
                raise RuntimeError('gateway exited before readiness')
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=.5)
            try:
                connection.request('GET', '/_health', headers={'Authorization': 'Bearer ' + token})
                if connection.getresponse().status == 200:
                    ready = True
                    break
            except OSError:
                time.sleep(.05)
            finally:
                connection.close()
        if not ready:
            raise RuntimeError('gateway did not become ready')
        version = subprocess.check_output([str(executable), '--version'], text=True, timeout=15).strip()
        nonce = 'warden-native-' + os.urandom(8).hex()
        prompt = ('Validate this synthetic MCP connection. Call mcp__warden-example__echo with value "' + nonce
                  + '", then mcp__warden-example__sleep with seconds 2 and value "native-delay", '
                  'then mcp__warden-example__callback. Use each tool exactly once in that order. '
                  'Return a concise report with each value, backend PID, and whether all three succeeded. '
                  'Do not read files, run commands, use other tools or delegate.')
        command = [str(executable), '--restricted', '--setting-sources', '',
            '--debug', 'mcp', '--debug-file', str(output / 'claude-mcp-debug.log'),
            '--strict-mcp-config', '--mcp-config', str(mcp), '--disable-slash-commands',
            '--tools', '', '--allowedTools', 'mcp__warden-example__echo,mcp__warden-example__sleep,mcp__warden-example__callback',
            '--permission-mode', 'dontAsk', '--permission-prompts', 'none',
            '--no-session-persistence', '--model', args.model, '--effort', 'low',
            '--system-prompt', 'You validate a synthetic local MCP test. Follow the user prompt exactly. Do not delegate.',
            '--output-format', 'stream-json', '--verbose', '-p']
        began = time.monotonic()
        environment = os.environ.copy()
        environment['MCP_TIMEOUT'] = str(args.startup_timeout * 1000)

        def health():
            if args.transport == 'stdio':
                spec = importlib.util.spec_from_file_location('native_warden_status', ROOT / 'warden.py')
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module.get_stdio_status(state)
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
            try:
                connection.request('GET', '/_health', headers={'Authorization': 'Bearer ' + token})
                return json.loads(connection.getresponse().read())
            finally:
                connection.close()

        reconnect = None
        if args.scenario == 'reconnect':
            returncode, stdout, stderr, reconnect = persistent_turns(
                executable, args.model, cwd, output, args.timeout, health, environment, json.loads(entry))
        else:
            client = subprocess.Popen([*command, prompt], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0, env=environment)
            stdout, stderr = client.communicate(timeout=args.timeout)
            returncode = client.returncode
        elapsed = time.monotonic() - began
        (output / 'claude-output.jsonl').write_bytes(stdout)
        (output / 'claude-stderr.log').write_bytes(stderr)
        messages = [json.loads(line) for line in stdout.decode('utf-8').splitlines() if line.strip()]
        calls, replies = [], []
        for message in messages:
            for block in message.get('message', {}).get('content', []):
                if isinstance(block, dict) and block.get('type') == 'tool_use':
                    calls.append({'id': block.get('id'), 'name': block.get('name'), 'input': block.get('input')})
                if isinstance(block, dict) and block.get('type') == 'tool_result':
                    replies.append({'tool_use_id': block.get('tool_use_id'), 'is_error': block.get('is_error', False), 'content': block.get('content')})
        final = next((message for message in reversed(messages) if message.get('type') == 'result'), {})
        events_file = state / 'run' / 'events.jsonl'
        events = [json.loads(line) for line in events_file.read_text().splitlines()] if events_file.exists() else []
        parsed = []
        for reply in replies:
            try:
                parsed.append(json.loads(reply['content']))
            except (ValueError, TypeError):
                pass
        if args.scenario == 'basic':
            checks = {'exact_tool_order': [call['name'] for call in calls] == [
                'mcp__warden-example__echo', 'mcp__warden-example__sleep', 'mcp__warden-example__callback'],
                'correct_values': [result.get('value') for result in parsed] == [nonce, 'native-delay', 'callback-completed'],
                'requested_inputs': len(calls) == 3 and calls[0]['input'].get('value') == nonce
                    and calls[1]['input'].get('seconds') == 2,
                'same_backend': len(parsed) == 3 and len({result.get('pid') for result in parsed}) == 1,
                'no_tool_errors': len(replies) == 3 and not any(reply['is_error'] for reply in replies)}
        else:
            checks = {'expiry_observed': reconnect['expiry_observed'],
                'before_and_after_values': [result.get('value') for result in parsed] == ['native-before-expiry', 'native-after-expiry'],
                'different_backend': len(parsed) == 2 and parsed[0].get('pid') != parsed[1].get('pid'),
                'no_tool_errors': len(replies) == 2 and not any(reply['is_error'] for reply in replies)}
            if args.transport == 'stdio':
                checks['connection_owner_unchanged'] = reconnect.get('stdio_owner_before') is not None \
                    and reconnect.get('stdio_owner_before') == reconnect.get('stdio_owner_after')
        checks['backend_pids_observed'] = bool(parsed) and all(type(result.get('pid')) is int and result['pid'] > 0 for result in parsed)
        summary = {'client': version, 'model_requested': args.model, 'exit_code': returncode,
            'elapsed_seconds': elapsed, 'tool_calls': calls, 'tool_results': replies,
            'result_subtype': final.get('subtype'), 'is_error': final.get('is_error'),
            'result': final.get('result'), 'usage': final.get('usage'), 'model_usage': final.get('modelUsage'),
            'bridge_starts': sum(event['event'] == 'bridge_started' for event in events),
            'output_sha256': hashlib.sha256(stdout).hexdigest(), 'stderr_bytes': len(stderr),
            'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'scenario': args.scenario, 'checks': checks,
            'mcp_startup_timeout_seconds': args.startup_timeout,
            'transport': args.transport, 'lifecycle': reconnect,
            'startup_aborted_sessions': sum(event['event'] == 'session_closed' and event.get('reason') == 'initialize_interrupted' for event in events),
            'scope': 'Claude Code installed client and existing subscription login; synthetic backend only'}
        (output / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
        print(json.dumps(summary))
        return 0 if returncode == 0 and all(checks.values()) and not final.get('is_error', True) else 1
    finally:
        if client and client.poll() is None:
            client.terminate()
            client.wait(timeout=5)
        if gateway and gateway.stdin:
            gateway.stdin.close()
        if gateway:
            try:
                gateway.wait(timeout=15)
            except subprocess.TimeoutExpired:
                gateway.terminate()
                gateway.wait(timeout=5)
        log.close()


if __name__ == '__main__':
    raise SystemExit(main())
