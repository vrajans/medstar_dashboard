"""
login_page.py — InsightHub branded login page.
Professional navy + blue design. Zero pharmacy / MedStar references.
Uses token replacement (not .format()) so CSS braces are safe.
"""

LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>InsightHub — Sign In</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}

body{
  background:#0F172A;
  min-height:100vh;
  display:flex;
  align-items:center;
  justify-content:center;
  font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI','Helvetica Neue',sans-serif;
  -webkit-font-smoothing:antialiased;
}

/* Subtle grid background */
body::before{
  content:'';
  position:fixed;inset:0;
  background-image:
    linear-gradient(rgba(37,99,235,0.05) 1px,transparent 1px),
    linear-gradient(90deg,rgba(37,99,235,0.05) 1px,transparent 1px);
  background-size:48px 48px;
  pointer-events:none;
}

/* Glow blob */
body::after{
  content:'';
  position:fixed;
  top:-20%;left:50%;transform:translateX(-50%);
  width:700px;height:500px;
  background:radial-gradient(ellipse,rgba(37,99,235,0.18) 0%,transparent 70%);
  pointer-events:none;
}

.wrap{
  position:relative;z-index:1;
  width:100%;max-width:420px;
  padding:24px;
}

/* Product badge at top */
.product-header{
  text-align:center;
  margin-bottom:28px;
}
.product-logo{
  display:inline-flex;
  align-items:center;
  justify-content:center;
  width:48px;height:48px;
  background:linear-gradient(135deg,#2563EB,#0EA5E9);
  border-radius:14px;
  font-size:22px;
  margin-bottom:12px;
  box-shadow:0 8px 24px rgba(37,99,235,0.35);
}
.product-name{
  font-size:1.55rem;
  font-weight:800;
  color:#FFFFFF;
  letter-spacing:-0.5px;
  line-height:1;
}
.product-tagline{
  font-size:0.78rem;
  color:#64748B;
  margin-top:4px;
}

/* Card */
.card{
  background:#1E293B;
  border:1px solid rgba(255,255,255,0.08);
  border-radius:16px;
  padding:32px 32px 28px;
  box-shadow:0 24px 64px rgba(0,0,0,0.4);
}

.card-title{
  font-size:1.05rem;
  font-weight:700;
  color:#F1F5F9;
  margin-bottom:4px;
}
.card-sub{
  font-size:0.78rem;
  color:#64748B;
  margin-bottom:28px;
}

label{
  display:block;
  font-size:0.69rem;
  font-weight:600;
  text-transform:uppercase;
  letter-spacing:0.07em;
  color:#94A3B8;
  margin-bottom:5px;
  margin-top:18px;
}

input[type=text],input[type=password]{
  width:100%;
  padding:11px 14px;
  font-size:0.9rem;
  background:#0F172A;
  border:1px solid rgba(255,255,255,0.10);
  border-radius:8px;
  outline:none;
  color:#F1F5F9;
  transition:border-color 0.15s,box-shadow 0.15s;
  font-family:inherit;
}
input[type=text]::placeholder,input[type=password]::placeholder{
  color:#334155;
}
input[type=text]:focus,input[type=password]:focus{
  border-color:#2563EB;
  box-shadow:0 0 0 3px rgba(37,99,235,0.20);
}

.btn{
  display:block;
  width:100%;
  margin-top:26px;
  padding:12px;
  font-size:0.95rem;
  font-weight:700;
  background:linear-gradient(135deg,#2563EB,#1D4ED8);
  color:#fff;
  border:none;
  border-radius:9px;
  cursor:pointer;
  transition:opacity 0.15s,transform 0.1s,box-shadow 0.15s;
  letter-spacing:0.01em;
  font-family:inherit;
  box-shadow:0 4px 16px rgba(37,99,235,0.30);
}
.btn:hover{
  opacity:0.93;
  transform:translateY(-1px);
  box-shadow:0 6px 20px rgba(37,99,235,0.40);
}
.btn:active{transform:translateY(0);opacity:1}

.error{
  background:rgba(220,38,38,0.12);
  border:1px solid rgba(220,38,38,0.35);
  border-radius:8px;
  color:#FCA5A5;
  font-size:0.8rem;
  padding:10px 14px;
  margin-top:0;
  margin-bottom:16px;
  display:flex;
  align-items:center;
  gap:8px;
}

.divider{
  border:none;
  border-top:1px solid rgba(255,255,255,0.06);
  margin:24px 0 18px;
}

.footer-links{
  display:flex;
  justify-content:center;
  gap:16px;
  font-size:0.72rem;
  color:#475569;
}
.footer-links a{
  color:#64748B;
  text-decoration:none;
  transition:color 0.15s;
}
.footer-links a:hover{color:#94A3B8}

.trial-prompt{
  text-align:center;
  font-size:0.78rem;
  color:#64748B;
  margin-top:20px;
}
.trial-prompt a{
  color:#60A5FA;
  text-decoration:none;
  font-weight:500;
}
.trial-prompt a:hover{color:#93C5FD}
</style>
</head>
<body>
<script src="/cookie-consent.js" defer></script>
<div class="wrap">

  <div class="product-header">
    <div class="product-logo">&#x1F4CA;</div>
    <div class="product-name">InsightHub</div>
    <div class="product-tagline">Business Analytics Platform</div>
  </div>

  <div class="card">
    <div class="card-title">Sign in to your account</div>
    <div class="card-sub">Enter your credentials to access your dashboard</div>

    __ERROR_BLOCK__

    <form method="POST" action="/login">
      <input type="hidden" name="next" value="__NEXT_URL__"/>

      <label for="username">Username or Email</label>
      <input type="text" id="username" name="username"
             placeholder="you@company.com"
             autocomplete="username"
             value="__USERNAME_VAL__"
             required autofocus/>

      <label for="password">Password</label>
      <input type="password" id="password" name="password"
             placeholder="&#x2022;&#x2022;&#x2022;&#x2022;&#x2022;&#x2022;&#x2022;&#x2022;"
             autocomplete="current-password" required/>

      <button type="submit" class="btn">Sign In &rarr;</button>
    </form>

    <hr class="divider">

    <!-- Google SSO button (hidden if GOOGLE_CLIENT_ID not set) -->
    __GOOGLE_BTN__

    <div class="footer-links">
      <a href="/pricing">View pricing</a>
      <a href="/forgot-password">Forgot password?</a>
      <a href="/landing">About InsightHub</a>
    </div>
  </div>

  <div class="trial-prompt">
    Don't have an account? <a href="/signup">Start your free 14-day trial</a>
  </div>

  <!-- Demo credentials — remove in production or set DEMO_MODE=0 -->
  __DEMO_HINT__

</div>
</body>
</html>"""


import os as _os

_DEMO_MODE = _os.getenv("DEMO_MODE", "1") != "0"
_GOOGLE_SSO_ENABLED = bool(_os.getenv("GOOGLE_CLIENT_ID", ""))

_GOOGLE_BTN_HTML = """
<div style="text-align:center;margin-bottom:12px">
  <span style="display:block;font-size:0.72rem;color:#475569;margin-bottom:10px">
    — or continue with —
  </span>
  <a href="/google-login"
     style="display:inline-flex;align-items:center;gap:10px;
            background:#fff;color:#1E293B;
            border:1px solid rgba(255,255,255,0.18);
            border-radius:8px;padding:10px 20px;
            font-size:0.88rem;font-weight:600;text-decoration:none;
            transition:box-shadow 0.15s;box-shadow:0 2px 8px rgba(0,0,0,0.25)">
    <svg width="18" height="18" viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.18 1.48-4.97 2.36-8.16 2.36-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>
    </svg>
    Sign in with Google
  </a>
</div>"""

_DEMO_HINT_HTML = """
<div style="margin-top:16px;background:rgba(37,99,235,0.10);border:1px solid rgba(37,99,235,0.25);
     border-radius:10px;padding:12px 16px;font-size:0.75rem;color:#94A3B8;line-height:1.7">
  <strong style="color:#60A5FA">Demo credentials</strong><br>
  Admin: <code style="color:#F1F5F9">admin</code> / <code style="color:#F1F5F9">admin123</code><br>
  Viewer: <code style="color:#F1F5F9">viewer</code> / <code style="color:#F1F5F9">viewer123</code>
</div>"""


def render_login(error=None, next_url="/", username_val=""):
    """Return the full login page HTML with substituted values."""
    error_block = ""
    if error:
        error_block = (
            '<div class="error">'
            '<span>&#9888;</span>'
            '<span>' + str(error) + '</span>'
            '</div>'
        )
    demo_hint  = _DEMO_HINT_HTML if _DEMO_MODE else ""
    google_btn = _GOOGLE_BTN_HTML if _GOOGLE_SSO_ENABLED else ""
    return (
        LOGIN_HTML
        .replace("__ERROR_BLOCK__",  error_block)
        .replace("__NEXT_URL__",     next_url)
        .replace("__USERNAME_VAL__", username_val)
        .replace("__DEMO_HINT__",    demo_hint)
        .replace("__GOOGLE_BTN__",   google_btn)
    )
