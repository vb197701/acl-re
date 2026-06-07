#!/usr/bin/env python3
"""
ACLClouds 自动续期脚本 (Playwright 全程浏览器版 · 多账号)
支持最多 4 个账号，通过编号 Secret 区分：
ACCOUNT1_EMAIL / ACCOUNT1_PASSWORD
"""

import os
import re
import sys
import json
import time
import traceback
import subprocess
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.parse import urlparse

PROXY_SERVER = os.environ.get("PROXY_SERVER", "socks5://127.0.0.1:10808").strip()
ENABLE_VIDEO = os.environ.get("ENABLE_VIDEO", "false").strip().lower() == "true"

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
WXPUSHER_APPTOKEN = os.environ.get("WXPUSHER_APPTOKEN", "").strip()
WXPUSHER_UID = os.environ.get("WXPUSHER_UID", "").strip()

RENEW_THRESHOLD_DAYS = float(os.environ.get("RENEW_THRESHOLD_DAYS", "2").strip())
NEXT_RUN_FILE = os.environ.get("NEXT_RUN_FILE", "next_run.txt").strip()
BASE_URL = "https://dash.aclclouds.com"
LOGIN_URL = f"{BASE_URL}/auth/login"
EVENT_NAME = os.environ.get("GITHUB_EVENT_NAME", "").strip()


def mask_email(email: str) -> str:
    if not email or "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    local_m = local[0] + "**" if len(local) > 1 else "**"
    parts = domain.split(".")
    domain_m = parts[0][0] + "*" * (len(parts[0]) - 1) if parts[0] else "***"
    suffix = "." + ".".join(parts[1:]) if len(parts) > 1 else ""
    return f"{local_m}@{domain_m}{suffix}"


def mask_ip(ip: str) -> str:
    parts = ip.strip().split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.*.*"
    return "***"


def load_accounts():
    accounts = []
    for i in range(1, 5):
        email = os.environ.get(f"ACCOUNT{i}_EMAIL", "").strip()
        password = os.environ.get(f"ACCOUNT{i}_PASSWORD", "").strip()
        if email and password:
            accounts.append({
                "index": i,
                "email": email,
                "password": password,
                "email_masked": mask_email(email),
            })
    return accounts


def log(msg):
    print(f"[INFO] {msg}", flush=True)


def log_warn(msg):
    print(f"[WARN] {msg}", flush=True)


def log_error(msg):
    print(f"[ERROR] {msg}", flush=True)


def get_outbound_ip():
    try:
        data = urlopen("https://cloudflare.com/cdn-cgi/trace", timeout=8).read().decode()
        for line in data.splitlines():
            if line.startswith("ip="):
                raw = line.strip().replace("ip=", "")
                return f"ip={mask_ip(raw)}"
    except Exception as e:
        return f"ip=获取失败({e})"
    return "ip=未知"


def get_proxy_host_port():
    parsed = urlparse(PROXY_SERVER)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 10808
    return host, port


def get_proxy_ip():
    try:
        host, port = get_proxy_host_port()
        result = subprocess.run(
            ["curl", "-s", "--max-time", "8", "--socks5-hostname", f"{host}:{port}", "ifconfig.me"],
            capture_output=True,
            text=True,
            timeout=12
        )
        raw = result.stdout.strip()
        return mask_ip(raw) if result.returncode == 0 and raw else "获取失败"
    except Exception as e:
        return f"获取失败({e})"


def send_tg(text: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        body = json.dumps({
            "chat_id": TG_CHAT_ID,
            "text": text,
            "parse_mode": "HTML"
        }).encode()
        req = Request(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            data=body,
            headers={"Content-Type": "application/json"}
        )
        urlopen(req, timeout=15)
        log("TG 推送成功")
    except Exception as e:
        log_warn(f"TG 推送失败: {e}")


def send_wxpusher(text: str):
    if not WXPUSHER_APPTOKEN or not WXPUSHER_UID:
        return
    try:
        payload = {
            "appToken": WXPUSHER_APPTOKEN,
            "content": text,
            "summary": "ACLClouds 续期通知",
            "contentType": 1,
            "uids": [WXPUSHER_UID]
        }
        req = Request(
            "https://wxpusher.zjiecode.com/api/send/message",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}
        )
        result = json.loads(urlopen(req, timeout=15).read().decode())
        if result.get("code") == 1000:
            log("wxpusher 推送成功")
        else:
            log_warn(f"wxpusher 返回错误: {result}")
    except Exception as e:
        log_warn(f"wxpusher 推送失败: {e}")


def send_all_push(text: str):
    send_tg(text)
    send_wxpusher(text)


def parse_expire_datetime(text):
    if text is None:
        return None
    s = str(text).strip()
    if re.search(r"\d{4}-\d{2}-\d{2}", s):
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            return None
    return None


def parse_expires(text):
    if text is None:
        return None
    s = str(text).strip()

    dt = parse_expire_datetime(s)
    if dt:
        return (dt - datetime.now(timezone.utc)).total_seconds() / 86400

    try:
        return float(s) / 86400
    except Exception:
        pass

    sl = s.lower()
    days = hours = minutes = 0.0

    m = re.search(r"(\d+(?:\.\d+)?)\s*[dj]", sl)
    if m:
        days = float(m.group(1))

    m = re.search(r"(\d+(?:\.\d+)?)\s*h", sl)
    if m:
        hours = float(m.group(1))

    m = re.search(r"(\d+(?:\.\d+)?)\s*m(?!o)", sl)
    if m:
        minutes = float(m.group(1))

    total = days + hours / 24 + minutes / 1440
    return total if total > 0 else None


def compute_next_run(all_project_records, threshold_days):
    now = datetime.now(timezone.utc)
    candidates = []

    for item in all_project_records:
        expires_at = item.get("expires_at")
        dt = parse_expire_datetime(expires_at)
        if not dt:
            continue

        check_at = dt - timedelta(days=threshold_days)
        if check_at < now:
            check_at = now
        candidates.append(check_at)

    if candidates:
        return min(candidates)

    return now + timedelta(days=1)


def save_next_run(next_run_dt):
    next_run_dt = next_run_dt.astimezone(timezone.utc).replace(microsecond=0)
    raw = next_run_dt.isoformat().replace("+00:00", "Z")
    with open(NEXT_RUN_FILE, "w", encoding="utf-8") as f:
        f.write(raw + "\n")
    log(f"已写入 {NEXT_RUN_FILE}: {raw}")


def screenshot(page, name: str):
    os.makedirs("screenshots", exist_ok=True)
    path = f"screenshots/{name}.png"
    try:
        page.evaluate(
            """() => {
                const blur = el => { el.style.filter = 'blur(8px)'; };

                document.querySelectorAll('input').forEach(blur);

                const headerSelectors = [
                  'header button', 'header [role="button"]',
                  'nav button', 'nav [role="button"]',
                  'span.username', '[class*="username"]', '[class*="user-name"]',
                  '[class*="UserName"]', '[class*="userName"]',
                  '.user-info', '.header-user', '.navbar .user',
                  '.account-name', '.text-sm.font-medium',
                  '[class*="avatar"] + *', '[class*="Avatar"] + *',
                ];
                headerSelectors.forEach(sel => {
                  try { document.querySelectorAll(sel).forEach(blur); } catch(e) {}
                });

                ['header', 'nav', '.topbar', '.top-bar', '#header', '#nav'].forEach(sel => {
                  try {
                    document.querySelectorAll(sel).forEach(el => {
                      el.querySelectorAll('span, p, a, button, div').forEach(child => {
                        if (child.children.length === 0 && child.textContent.trim()) {
                          blur(child);
                        }
                      });
                    });
                  } catch(e) {}
                });

                document.querySelectorAll('table td, table th').forEach(td => {
                  if (td.tagName !== 'TH' && /[0-9]/.test(td.textContent)) { blur(td); }
                });

                document.querySelectorAll(
                  '[class*="service"] [class*="name"], [class*="server"] [class*="name"],' +
                  '[class*="project"] [class*="name"], [class*="node"], [class*="identifier"],' +
                  '[class*="expire"], [class*="renew"], [class*="date"]'
                ).forEach(blur);

                document.querySelectorAll('[class*="address"], [class*="ip"], [class*="host"]').forEach(blur);

                document.querySelectorAll('h1, h2, h3').forEach(el => {
                  el.querySelectorAll('span, strong, b').forEach(blur);
                });
            }"""
        )
    except Exception:
        pass

    try:
        page.screenshot(path=path, full_page=True)
        log(f"截图已保存: {path}")
    except Exception as e:
        log_warn(f"截图失败 {path}: {e}")


def run_account(account: dict):
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    idx = account["index"]
    email = account["email"]
    password = account["password"]
    email_masked = account["email_masked"]
    tag = f"账号{idx}({email_masked})"

    log(f"\n{'=' * 50}")
    log(f"开始处理 {tag}")
    log(f"{'=' * 50}")

    renewed_list, skipped_list, failed_list = [], [], []
    account_projects = []

    with sync_playwright() as p:
        os.makedirs("screenshots", exist_ok=True)
        browser = p.chromium.launch(
            args=["--no-sandbox", "--disable-setuid-sandbox"],
            proxy={"server": PROXY_SERVER},
        )

        ctx_kwargs = dict(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/148.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
        )

        if ENABLE_VIDEO:
            ctx_kwargs["record_video_dir"] = "screenshots/"
            ctx_kwargs["record_video_size"] = {"width": 1280, "height": 800}
            log(f"[{tag}] 录屏已开启")

        ctx = browser.new_context(**ctx_kwargs)
        page = ctx.new_page()

        try:
            log(f"[{tag}] 导航到登录页: {LOGIN_URL}")
            page.goto(LOGIN_URL, timeout=60000)
            page.wait_for_load_state("networkidle", timeout=30000)
            screenshot(page, f"acct{idx}_01_login_page")

            log(f"[{tag}] 填写登录表单...")
            email_selectors = [
                "input[type='email']",
                "input[name='user']",
                "input[name='email']",
                "input[placeholder*='mail']",
                "input[placeholder*='Email']",
                "input:first-of-type",
            ]

            email_filled = False
            for sel in email_selectors:
                try:
                    page.wait_for_selector(sel, timeout=3000)
                    page.fill(sel, email)
                    log(f"[{tag}] 邮箱字段使用选择器: {sel}")
                    email_filled = True
                    break
                except Exception:
                    continue

            if not email_filled:
                screenshot(page, f"acct{idx}_02_no_email_field")
                raise RuntimeError("找不到邮箱输入框")

            password_filled = False
            for sel in ["input[type='password']", "input[name='password']"]:
                try:
                    page.wait_for_selector(sel, timeout=3000)
                    page.fill(sel, password)
                    password_filled = True
                    break
                except Exception:
                    continue

            if not password_filled:
                screenshot(page, f"acct{idx}_02_no_password_field")
                raise RuntimeError("找不到密码输入框")

            screenshot(page, f"acct{idx}_02_form_filled")

            log(f"[{tag}] 点击 captcha 复选框...")
            page.click("div.auth-captcha-inner", timeout=10000)
            try:
                page.wait_for_selector(
                    "div.auth-captcha-box.verified, div.auth-captcha-inner[aria-checked='true']",
                    timeout=10000
                )
                log(f"[{tag}] captcha 验证通过")
            except Exception:
                log_warn(f"[{tag}] captcha 未检测到 verified，继续提交")

            screenshot(page, f"acct{idx}_02b_captcha")

            submitted = False
            for sel in [
                "button[type='submit']",
                "button:has-text('Login')",
                "button:has-text('登录')",
                "button:has-text('Sign in')",
                "input[type='submit']",
            ]:
                try:
                    page.click(sel, timeout=3000)
                    submitted = True
                    break
                except Exception:
                    continue

            if not submitted:
                raise RuntimeError("找不到登录提交按钮")

            page.wait_for_load_state("networkidle", timeout=30000)
            screenshot(page, f"acct{idx}_03_after_submit")

            try:
                page.wait_for_url(lambda url: "login" not in url, timeout=20000)
                log(f"[{tag}] 登录成功，URL: {page.url}")
            except PWTimeout:
                screenshot(page, f"acct{idx}_03_login_timeout")
                raise RuntimeError(f"登录超时，仍在: {page.url}")

            screenshot(page, f"acct{idx}_04_after_login")

            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            time.sleep(3)

            result = page.evaluate(
                """async () => {
                    const r = await fetch('/api/client', {headers: {'Accept': 'application/json'}});
                    return {status: r.status, body: await r.text()};
                }"""
            )
            if result["status"] != 200:
                raise RuntimeError(f"获取项目列表失败 HTTP {result['status']}")

            data = json.loads(result["body"])
            projects = [item["attributes"] for item in data.get("data", []) if item.get("attributes")]
            account_projects.extend(projects)

            log(f"[{tag}] 找到 {len(projects)} 个项目")

            if not projects:
                log_warn(f"[{tag}] 项目列表为空")
                try:
                    screenshot(page, f"acct{idx}_05_empty_projects")
                except Exception:
                    pass
                return renewed_list, skipped_list, failed_list, account_projects

            for project in projects:
                name = project.get("name", "未知项目")
                identifier = project.get("identifier", "")
                raw_expires = project.get("expires_at")
                remaining = parse_expires(raw_expires)

                if remaining is None:
                    failed_list.append(f"{tag} · {name}（无法解析过期时间）")
                    continue

                log(f"[{tag}] [{name}] 剩余 {remaining:.2f} 天")

                if remaining >= RENEW_THRESHOLD_DAYS:
                    skipped_list.append(f"{tag} · {name}（剩余 {remaining:.1f} 天）")
                    continue

                try:
                    renew_url = f"/api/client/servers/{identifier}/upgrade/renew"
                    renew_result = page.evaluate(
                        f"""async () => {{
                            const xsrf = decodeURIComponent(
                                document.cookie.split('; ')
                                .find(c => c.startsWith('XSRF-TOKEN='))
                                ?.split('=')[1] || ''
                            );
                            const r = await fetch('{renew_url}', {{
                                method: 'POST',
                                headers: {{'Accept': 'application/json', 'X-XSRF-TOKEN': xsrf}}
                            }});
                            return {{status: r.status, body: await r.text()}};
                        }}"""
                    )

                    if renew_result["status"] == 200:
                        time.sleep(2)
                        new_result = page.evaluate(
                            """async () => {
                                const r = await fetch('/api/client', {headers: {'Accept': 'application/json'}});
                                return await r.json();
                            }"""
                        )
                        new_expires = None
                        for item in new_result.get("data", []):
                            attrs = item.get("attributes", {})
                            if attrs.get("identifier") == identifier:
                                new_expires = attrs.get("expires_at")
                                project["expires_at"] = new_expires
                                break

                        if new_expires:
                            new_remaining = parse_expires(new_expires)
                            if new_remaining is not None:
                                renewed_list.append(f"{tag} · {name}（{remaining:.1f}天 → {new_remaining:.1f}天）")
                            else:
                                renewed_list.append(f"{tag} · {name}（续期成功）")
                        else:
                            renewed_list.append(f"{tag} · {name}（续期前 {remaining:.1f} 天）")
                    else:
                        body = renew_result["body"]
                        try:
                            err = json.loads(body).get("error", "unknown")
                        except Exception:
                            err = body[:80]
                        raise RuntimeError(f"续期失败: {err}")

                except Exception as e:
                    log_error(f"[{tag}][{name}] 续期异常: {e}")
                    failed_list.append(f"{tag} · {name}（{str(e)[:80]}）")

            try:
                screenshot(page, f"acct{idx}_05_final")
            except Exception:
                pass

        except Exception as e:
            try:
                screenshot(page, f"acct{idx}_99_error")
            except Exception:
                pass
            failed_list.append(f"{tag} · 账号级异常: {str(e)[:120]}")

        finally:
            if ENABLE_VIDEO:
                try:
                    page.video.save_as(f"screenshots/acct{idx}_video.webm")
                except Exception:
                    pass
            try:
                ctx.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass

    return renewed_list, skipped_list, failed_list, account_projects


if __name__ == "__main__":
    accounts = load_accounts()
    if not accounts:
        log_error("未找到任何账号！请设置 ACCOUNT1_EMAIL / ACCOUNT1_PASSWORD 等环境变量")
        sys.exit(1)

    log(f"[网络] 直连出口 IP: {get_outbound_ip()}")
    log(f"[网络] 代理出口 IP: {get_proxy_ip()}")
    log(f"[网络] Playwright 代理: {PROXY_SERVER}")
    log(f"[策略] 续期阈值: {RENEW_THRESHOLD_DAYS} 天")
    log(f"[策略] next_run 文件: {NEXT_RUN_FILE}")
    log(f"共 {len(accounts)} 个账号待处理")
    log(f"录屏: {'开启' if ENABLE_VIDEO else '关闭'}")

    all_renewed, all_skipped, all_failed = [], [], []
    all_project_records = []

    for account in accounts:
        try:
            r, s, f, projects = run_account(account)
            all_renewed.extend(r)
            all_skipped.extend(s)
            all_failed.extend(f)
            all_project_records.extend(projects)
        except Exception as ex:
            em = account["email_masked"]
            log_error(f"账号{account['index']} 顶层异常: {ex}")
            traceback.print_exc()
            all_failed.append(f"账号{account['index']}({em}) · 顶层异常: {str(ex)[:100]}")

    try:
        next_run_dt = compute_next_run(all_project_records, RENEW_THRESHOLD_DAYS)
        save_next_run(next_run_dt)
    except Exception as e:
        log_warn(f"写入 {NEXT_RUN_FILE} 失败: {e}")

    log("=" * 50)
    log(f"续期成功: {len(all_renewed)} 个")
    log(f"无需续期: {len(all_skipped)} 个")
    log(f"失败/异常: {len(all_failed)} 个")

    if all_renewed:
        lines = ["✅ ACLClouds 自动续期成功", ""]
        lines += [f"• {i}" for i in all_renewed]
        if all_failed:
            lines += ["", "⚠️ 以下项目失败："]
            lines += [f"• {i}" for i in all_failed]
        if os.path.exists(NEXT_RUN_FILE):
            try:
                next_run_raw = open(NEXT_RUN_FILE, "r", encoding="utf-8").read().strip()
                lines += ["", f"下次检查时间(UTC): {next_run_raw}"]
            except Exception:
                pass
        lines += ["", "ACLClouds Auto Renew"]
        send_all_push("\n".join(lines))
    elif all_failed:
        lines = ["❌ ACLClouds 续期失败", ""]
        lines += [f"• {i}" for i in all_failed]
        if os.path.exists(NEXT_RUN_FILE):
            try:
                next_run_raw = open(NEXT_RUN_FILE, "r", encoding="utf-8").read().strip()
                lines += ["", f"下次检查时间(UTC): {next_run_raw}"]
            except Exception:
                pass
        lines += ["", "ACLClouds Auto Renew"]
        send_all_push("\n".join(lines))
    else:
        log("所有账号均无需续期，不发送推送")
        if EVENT_NAME == "workflow_dispatch":
            try:
                next_run_raw = open(NEXT_RUN_FILE, "r", encoding="utf-8").read().strip()
                send_all_push(f"ℹ️ ACLClouds 本次无需续期\n\n下次检查时间(UTC): {next_run_raw}\n\nACLClouds Auto Renew")
            except Exception:
                pass
