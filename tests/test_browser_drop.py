"""Run `python tests/test_browser_drop.py` with Playwright and Chrome installed."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import time
from unittest.mock import patch

import httpx
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.sftp_stub import SftpStub


@contextmanager
def serve_app():
    # Passing an already bound socket keeps the ephemeral port reserved at startup.
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config('app.main:app', log_level='warning'))
        thread = Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError('The local test server did not start.')
                time.sleep(0.05)
            yield f'http://127.0.0.1:{port}'
        finally:
            server.should_exit = True
            thread.join(timeout=10)


@contextmanager
def verify_upload(page):
    with page.expect_response(lambda response: response.url.endswith('/upload')) as upload:
        yield
    response = upload.value
    result = response.text()
    assert response.ok and json.loads(result.strip().splitlines()[-1]).get('ok'), result
    page.locator('#upload-bar').wait_for(state='hidden')


def drop_paths(page, cdp, paths):
    box = page.locator('#list-wrap').bounding_box()
    assert box, 'The remote file list is not visible.'
    drag = {'items': [], 'files': [str(path) for path in paths], 'dragOperationsMask': 1}
    with verify_upload(page):
        for event in ('dragEnter', 'dragOver', 'drop'):
            cdp.send('Input.dispatchDragEvent', {'type': event, 'data': drag, 'x': box['x'] + 20, 'y': box['y'] + 20})


def choose_from_disk(page, picker, paths):
    with page.expect_file_chooser() as chooser:
        page.locator(picker).click()
    chooser.value.set_files(paths)


def verify_upload_queue(page, source, dest):
    from playwright.sync_api import expect

    names = ['选中.txt', '略过.txt', '追加.bin', '取消.txt', '候选集/子目录/挑选.txt', '候选集/其余.txt']
    files = [source / name for name in names]
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(path.as_posix().encode('utf-8'))
    before = {path.relative_to(dest) for path in dest.rglob('*')}
    queue = page.locator('#queue-list')

    page.locator('#upload').click()
    choose_from_disk(page, '#queue-add-files', [files[3]])
    expect(queue.get_by_role('checkbox')).to_have_count(1)
    page.locator('#modal-cancel').click()
    expect(page.locator('#modal')).to_be_hidden()

    page.locator('#upload').click()
    expect(queue.get_by_role('checkbox')).to_have_count(0)
    choose_from_disk(page, '#queue-add-files', files[:2])  # Two paths in one native chooser action.
    choose_from_disk(page, '#queue-add-files', [files[2]])
    choose_from_disk(page, '#queue-browse-folder', source / '候选集')
    checks = queue.get_by_role('checkbox')
    expect(checks).to_have_count(5)
    expect(queue.locator('input:checked')).to_have_count(3)
    page.locator('#queue-all').click()
    expect(queue.locator('input:checked')).to_have_count(5)
    page.locator('#queue-all').click()
    expect(queue.locator('input:checked')).to_have_count(0)
    expect(page.locator('#modal-ok')).to_be_disabled()
    selected = [files[0], files[2], files[4]]
    for path in selected:
        queue.locator('label').filter(has_text=path.name).get_by_role('checkbox').check()
    with verify_upload(page):
        page.locator('#modal-ok').click()

    actual = {path.relative_to(dest) for path in dest.rglob('*')}
    assert actual == before | {Path(path.name) for path in selected}
    for path in selected:
        assert (dest / path.name).read_bytes() == path.read_bytes(), path.name


def test_browser_drop():
    from playwright.sync_api import sync_playwright

    with ExitStack() as stack:
        temp = Path(stack.enter_context(TemporaryDirectory()))
        stack.enter_context(patch.dict(os.environ, {
            'RF_HOME': str(temp / 'settings'), 'RF_SYNC_DISABLE': '1',
            'PYTHON_KEYRING_BACKEND': 'keyring.backends.null.Keyring',
        }))
        local, remote = temp / 'local', temp / 'remote'
        dest = remote / 'target'
        dest.mkdir(parents=True)
        (local / 'empty root').mkdir(parents=True)
        (local / '资料' / 'child' / '空目录').mkdir(parents=True)
        (local / 'two').mkdir()
        (local / '资料' / 'child' / 'zero.txt').touch()
        (local / '资料' / 'child' / 'same.bin').write_bytes(bytes(range(256)))
        (local / 'two' / 'same.bin').write_bytes(b'different')
        (local / '单 文件.txt').write_text('独立文件', encoding='utf-8')
        for index in range(125):  # Chromium returns at most 100 entries per readEntries call.
            (local / 'two' / f'{index}.txt').write_bytes(bytes([index]))

        stub = SftpStub(str(remote))
        stack.callback(stub.close)
        url = stack.enter_context(serve_app())
        client = stack.enter_context(httpx.Client(base_url=url, headers={'X-RF-Request': '1'}, trust_env=False))
        created = client.post('/api/sessions', json={
            'name': 'Drop test', 'host': '127.0.0.1', 'port': stub.port,
            'username': stub.username, 'rememberPassword': False,
        }).raise_for_status()
        session = '/api/sessions/' + created.json()['id']
        client.post(session + '/connect', json={'password': stub.password, 'remember': False}).raise_for_status()
        stack.callback(client.post, session + '/disconnect')

        playwright = stack.enter_context(sync_playwright())
        browser = playwright.chromium.launch(channel='chrome', headless=True)
        stack.callback(browser.close)
        page = browser.new_page()
        page.goto(url)
        page.get_by_text('Drop test', exact=True).click()
        page.locator('#rows tr').filter(has_text='target').dblclick()
        page.locator('#crumbs').get_by_role('button', name='target', exact=True).wait_for()
        cdp = page.context.new_cdp_session(page)
        drop_paths(page, cdp, [local / 'empty root'])
        assert (dest / 'empty root').is_dir() and not any((dest / 'empty root').iterdir())
        drop_paths(page, cdp, [local / '资料', local / 'two', local / '单 文件.txt'])
        assert {p.relative_to(local) for p in local.rglob('*')} == {p.relative_to(dest) for p in dest.rglob('*')}
        for path in local.rglob('*'):
            uploaded = dest / path.relative_to(local)
            assert uploaded.is_dir() == path.is_dir(), path
            if path.is_file():
                assert uploaded.read_bytes() == path.read_bytes(), path
        verify_upload_queue(page, temp / 'picker', dest)


if __name__ == '__main__':
    test_browser_drop()
    print('PASS: Chrome preserved the dropped tree and uploaded only checked files from the picker.')
