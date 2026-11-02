# AssignPad

Fast front end for WebAssign with a Desmos-style math editor (MathLive).
It syncs with WebAssign: you see your assignments and submit answers from AssignPad.

## How it works

- **Server** (Docker): FastAPI + headless Chromium (Playwright). It logs in to WebAssign with your Cengage email and password (environment variables), reads assignments and to submit answers on the real WebAssign page.
- **PWA**: installable on desktop and mobile. Data is cached, so pages open immediately.
- **Browser extension** (`extension/`, optional): sends your WebAssign session cookies to the server, if you do not want to store your Cengage password.
- **Answers**: you type in MathLive. The app converts LaTeX to MathML (the format MathType/WebAssign uses) and the server puts it in the WebAssign answer box, then clicks the WebAssign **Submit** button.

## Set up

1. Push this folder to a GitHub repo named `assignpad`. The GitHub Action builds `ghcr.io/daviied/assignpad:latest`.
2. In GitHub > Packages > assignpad > Package settings, set visibility to **Public** (or log in to ghcr.io on the server).
3. Paste `docker-compose.yml` into CasaOS (Custom Install > Import). Set `APP_PASSWORD`, `CENGAGE_EMAIL` and `CENGAGE_PASSWORD` in the CasaOS form. For the docker compose CLI, copy `.env.example` to `.env`. Never commit `.env`.
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

5. Open your server URL, log in with `APP_PASSWORD`, then install the app (browser menu > Install).

The server logs in to WebAssign at startup and again when the session ends. It keeps the session alive every 15 minutes.
Optional: instead of `CENGAGE_EMAIL`/`CENGAGE_PASSWORD`, load `extension/` (Chrome > `chrome://extensions` > Developer mode > Load unpacked) and click **Save and send session**.

## Security

- `APP_PASSWORD` is required (8+ characters). The server does not start without it.
- Login sets an HttpOnly, SameSite=Strict session cookie. 10 failed logins in 15 minutes from one IP are blocked.
- The extension uses the password as a Bearer token.
- Cengage email/password are only in environment variables. Your WebAssign cookies are stored in `/data/session.json` (mode 600). Protect the `/data` volume.

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
