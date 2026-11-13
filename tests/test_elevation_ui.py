"""Exercise both elevation methods in the desktop UI with a simulated SSH API."""
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright


def test_elevation_ui():
    static = Path(__file__).resolve().parents[1] / "static"
    session = {"id": "demo", "name": "Test server", "host": "test.local", "username": "user",
               "port": 22, "connected": True, "sudo": False, "elevationMethod": None}
    requests = []
    allow_sudo = False

    def respond(route):
        url = urlsplit(route.request.url)
        if url.path == "/":
            route.fulfill(path=static / "index.html", content_type="text/html")
        elif url.path.startswith("/static/"):
            route.fulfill(path=static / Path(url.path).name)
        elif url.path == "/api/sessions":
            route.fulfill(json=[session])
        elif url.path.endswith("/list"):
            path = parse_qs(url.query).get("path", ["/home/user"])[0]
            if path == "/private" and not session["sudo"]:
                route.fulfill(status=403, json={"error": "没有权限", "permissionDenied": True})
            else:
                entries = [] if path == "/private" else [{"name": "private", "path": "/private", "isDir": True}]
                route.fulfill(json={"path": path, "parent": "/", "entries": entries})
        elif url.path.endswith("/sudo"):
            payload = route.request.post_data_json
            requests.append(payload)
            if payload.get("method") == "sudo" and not allow_sudo:
                route.fulfill(status=401, json={"error": "当前账号不能使用 sudo", "sudoPassword": True})
                return
            session.update(sudo=payload["enable"], elevationMethod=payload.get("method"))
            route.fulfill(json={"ok": True, "sudo": session["sudo"], "elevationMethod": session["elevationMethod"]})
        elif url.path.endswith("/notices"):
            route.fulfill(json={"notices": []})
        else:
            route.fulfill(json={})

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        page.route("http://remote-folder.test/**", respond)
        page.goto("http://remote-folder.test/")
        page.get_by_text("Test server", exact=True).click()
        toolbar = page.locator("#sudo-btn")
        method = page.get_by_label("提权方式")
        confirm = page.locator("#modal-ok")

        toolbar.click()
        method.select_option("su")
        page.get_by_label("root 密码", exact=True).fill("temporary")
        page.locator("#modal-cancel").click()
        assert not requests
        expect(page.locator("#modal-form input[type=password]")).to_have_value("")

        page.get_by_text("private", exact=True).dblclick()
        expect(page.locator("#modal-message")).to_contain_text("当前权限不足")
        confirm.click()
        expect(page.locator("#modal-message")).to_contain_text("当前账号不能使用 sudo")
        assert requests == [{"enable": True, "method": "sudo", "password": "", "remember": False}]
        page.get_by_label("当前账号的 sudo 密码").fill("user-secret")
        page.get_by_label("记住 sudo 密码").check()
        method.select_option("su")
        expect(page.get_by_label("记住 sudo 密码")).to_be_hidden()
        root_password = page.get_by_label("root 密码", exact=True)
        expect(root_password).to_have_value("")
        confirm.click()
        assert len(requests) == 1
        root_password.fill("test-root-secret")
        confirm.click()
        expect(toolbar).to_have_text("关闭 su root")
        expect(page.locator("#status")).to_contain_text("su root")
        page.wait_for_function("state.path === '/private'")
        assert requests[-1] == {"enable": True, "method": "su", "password": "test-root-secret", "remember": False}
        toolbar.click()
        expect(toolbar).to_have_text("提升权限")
        page.wait_for_function("state.path === '/home/user'")

        allow_sudo = True
        toolbar.click()
        confirm.click()
        expect(toolbar).to_have_text("关闭 sudo")
        expect(page.locator("#status")).to_contain_text("root（sudo）")
        assert requests[-1] == {"enable": True, "method": "sudo", "password": "", "remember": False}
        browser.close()


if __name__ == "__main__":
    test_elevation_ui()
    print("PASS: sudo and su elevation choices, retry, status and original access.")
