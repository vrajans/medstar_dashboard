"""
login_page.py  -  InsightHub branded login HTML (served by Flask route).
No template engine needed -- pure Python string with .replace() substitution.
CSS curly braces are safe because we avoid .format() entirely.
"""

LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>InsightHub -- Sign In</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{
  background:linear-gradient(135deg,#0f2d1f 0%,#1e7e4b 55%,#134d2f 100%);
  min-height:100vh;display:flex;align-items:center;justify-content:center;
  font-family:'Segoe UI',system-ui,-apple-system,'Helvetica Neue',sans-serif;
}
.card{
  background:#fff;border-radius:18px;padding:2.6rem 2.4rem 2.2rem;
  width:100%;max-width:400px;
  box-shadow:0 20px 60px rgba(0,0,0,0.28),0 6px 20px rgba(0,0,0,0.16);
}
.logo{text-align:center;margin-bottom:0.5rem;font-size:2.6rem;line-height:1}
.brand{text-align:center;font-size:1.6rem;font-weight:800;color:#1e7e4b;
       letter-spacing:-0.5px;margin-bottom:0.1rem}
.tagline{text-align:center;font-size:0.78rem;color:#94a3b8;margin-bottom:2rem}
label{display:block;font-size:0.72rem;font-weight:700;text-transform:uppercase;
      letter-spacing:0.8px;color:#64748b;margin-bottom:0.35rem;margin-top:1.1rem}
input[type=text],input[type=password]{
  width:100%;padding:0.65rem 0.9rem;font-size:0.9rem;
  border:1.5px solid #e2e8f0;border-radius:8px;outline:none;
  transition:border-color 0.15s,box-shadow 0.15s;color:#0f172a;
}
input[type=text]:focus,input[type=password]:focus{
  border-color:#1e7e4b;box-shadow:0 0 0 3px rgba(30,126,75,0.12);
}
.btn{
  display:block;width:100%;margin-top:1.8rem;
  padding:0.78rem;font-size:0.95rem;font-weight:700;
  background:#1e7e4b;color:#fff;border:none;border-radius:8px;cursor:pointer;
  transition:background 0.15s,transform 0.1s;letter-spacing:0.2px;
}
.btn:hover{background:#196840;transform:translateY(-1px)}
.btn:active{transform:translateY(0)}
.error{
  background:#fef2f2;border:1px solid #fca5a5;border-radius:8px;
  color:#dc2626;font-size:0.8rem;padding:0.55rem 0.9rem;
  margin-top:1rem;display:flex;align-items:center;gap:6px;
}
.footer{text-align:center;margin-top:1.6rem;font-size:0.7rem;color:#cbd5e1}
</style>
</head>
<body>
<div class="card">
  <div class="logo">&#x1F3E5;</div>
  <div class="brand">InsightHub</div>
  <div class="tagline">Multi-Branch Analytics Platform</div>

  __ERROR_BLOCK__

  <form method="POST" action="/login">
    <input type="hidden" name="next" value="__NEXT_URL__"/>
    <label for="username">Username</label>
    <input type="text" id="username" name="username"
           placeholder="Enter username" autocomplete="username"
           value="__USERNAME_VAL__" required autofocus/>

    <label for="password">Password</label>
    <input type="password" id="password" name="password"
           placeholder="Enter password" autocomplete="current-password" required/>

    <button type="submit" class="btn">Sign In &rarr;</button>
  </form>

  <div class="footer">MedStar Pharmacy &mdash; Confidential &mdash; InsightHub v1.0</div>
</div>
</body>
</html>"""


def render_login(error=None, next_url="/", username_val=""):
    """Return the full login page HTML with substituted values.

    Uses simple token replacement (not .format()) so CSS curly braces
    like {box-sizing} never cause a KeyError.
    """
    error_block = ""
    if error:
        error_block = (
            '<div class="error">'
            '<span>&#9888;</span>'
            '<span>' + str(error) + '</span>'
            '</div>'
        )
    return (
        LOGIN_HTML
        .replace("__ERROR_BLOCK__",  error_block)
        .replace("__NEXT_URL__",     next_url)
        .replace("__USERNAME_VAL__", username_val)
    )
