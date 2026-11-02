# AssignPad

Fast front end for WebAssign with a Desmos-style math editor (MathLive).
It syncs with WebAssign: you see your assignments and submit answers from AssignPad.

## How it works

- **Server** (Docker): FastAPI + headless Chromium (Playwright). It uses your WebAssign session to read assignments and to submit answers on the real WebAssign page.
- **PWA**: installable on desktop and mobile. Data is cached, so pages open immediately.
- **Browser extension** (`extension/`): sends your WebAssign session cookies to your server. You do not store your password.
- **Answers**: you type in MathLive. The app converts LaTeX to MathML (the format MathType/WebAssign uses) and the server puts it in the WebAssign answer box, then clicks the WebAssign **Submit** button.

## Set up

1. Push this folder to a GitHub repo named `assignpad`. The GitHub Action builds `ghcr.io/<you>/assignpad:latest`.
2. In GitHub > Packages > assignpad > Package settings, set visibility to **Public** (or log in to ghcr.io on the server).
3. In `docker-compose.yml`, change `YOUR_GITHUB_USER` and `APP_PASSWORD`. Paste it into CasaOS (Custom Install > Import).
4. Put it behind nginx with HTTPS. Submits can take up to 60 s:

   ```nginx
   location / {
     proxy_pass http://CASAOS_IP:8080;
     proxy_set_header Host $host;
     proxy_set_header X-Real-IP $remote_addr;
     proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
     proxy_set_header X-Forwarded-Proto $scheme;
     proxy_read_timeout 120s;
   }
   ```

5. Install the extension: Chrome/Edge > `chrome://extensions` > Developer mode > **Load unpacked** > select `extension/`.
6. Log in to WebAssign in that browser. Open the extension, enter your server URL and `APP_PASSWORD`, click **Save and send session**.
7. Open your server URL, log in with `APP_PASSWORD`, then install the app (browser menu > Install).

The extension sends the session again when the cookies change and every 30 minutes. The server keeps the session alive every 15 minutes.
Optional fallback: set `CENGAGE_EMAIL` and `CENGAGE_PASSWORD` so the server logs in by itself when the session expires.

## Security

- `APP_PASSWORD` is required (8+ characters). The server does not start without it.
- Login sets an HttpOnly, SameSite=Strict session cookie. 10 failed logins in 15 minutes from one IP are blocked.
- The extension uses the password as a Bearer token.
- Your WebAssign cookies are stored in `/data/session.json` (mode 600). Protect the `/data` volume.

## Limits

- Supported answer types: math/numeric (MathType boxes), multiple choice (radio/checkbox), dropdowns. Graphing and other pads: use **Open in WebAssign**.
- If WebAssign changes its pages, the parser (`server/parse.py`) can need updates.
- Use of automated access can conflict with the Cengage terms of use. You accept that risk.

## Development

```bash
pip install -r requirements.txt && playwright install chromium
# put MathLive in public/vendor/mathlive (see Dockerfile)
cd server && DATA_DIR=../data APP_PASSWORD=devpassword1 COOKIE_SECURE=0 uvicorn main:app --port 8765
python -m pytest tests
```
