"""
landing_page.py — InsightHub marketing landing page.
Served at /landing via Flask route in app.py.
Zero pharmacy / MedStar references. Self-contained HTML + CSS.
"""

LANDING_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>InsightHub — Business Analytics for Growing Teams</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}

:root{
  --navy:#0F172A;
  --navy2:#1E293B;
  --blue:#2563EB;
  --blue-dark:#1D4ED8;
  --sky:#0EA5E9;
  --teal:#0D9488;
  --text:#F1F5F9;
  --muted:#94A3B8;
  --dim:#475569;
  --border:rgba(255,255,255,0.08);
  --radius:14px;
}

html{scroll-behavior:smooth}

body{
  background:var(--navy);
  color:var(--text);
  font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI','Helvetica Neue',sans-serif;
  -webkit-font-smoothing:antialiased;
  line-height:1.6;
  overflow-x:hidden;
}

/* ── NAVBAR ── */
nav{
  position:sticky;top:0;z-index:100;
  background:rgba(15,23,42,0.85);
  backdrop-filter:blur(12px);
  border-bottom:1px solid var(--border);
  padding:0 5%;
  height:62px;
  display:flex;align-items:center;justify-content:space-between;
}
.nav-brand{
  display:flex;align-items:center;gap:10px;
  font-size:1.1rem;font-weight:800;color:#fff;
  text-decoration:none;letter-spacing:-0.3px;
}
.nav-logo{
  width:34px;height:34px;border-radius:9px;
  background:linear-gradient(135deg,var(--blue),var(--sky));
  display:flex;align-items:center;justify-content:center;
  font-size:18px;flex-shrink:0;
}
.nav-links{display:flex;align-items:center;gap:28px}
.nav-links a{
  color:var(--muted);text-decoration:none;font-size:0.86rem;
  transition:color 0.15s;
}
.nav-links a:hover{color:#fff}
.btn-nav{
  padding:8px 18px;border-radius:8px;font-size:0.84rem;font-weight:600;
  background:var(--blue);color:#fff;text-decoration:none;
  transition:opacity 0.15s,transform 0.1s;
}
.btn-nav:hover{opacity:0.9;transform:translateY(-1px);color:#fff}

/* ── HERO ── */
.hero{
  min-height:92vh;
  display:flex;align-items:center;justify-content:center;
  text-align:center;
  padding:100px 5% 80px;
  position:relative;
  overflow:hidden;
}
.hero::before{
  content:'';position:absolute;inset:0;
  background-image:
    linear-gradient(var(--border) 1px,transparent 1px),
    linear-gradient(90deg,var(--border) 1px,transparent 1px);
  background-size:52px 52px;
  pointer-events:none;
}
.hero-glow{
  position:absolute;
  top:-10%;left:50%;transform:translateX(-50%);
  width:900px;height:700px;
  background:radial-gradient(ellipse,rgba(37,99,235,0.22) 0%,transparent 68%);
  pointer-events:none;
}
.hero-content{position:relative;z-index:1;max-width:820px;margin:0 auto}
.hero-badge{
  display:inline-flex;align-items:center;gap:7px;
  background:rgba(37,99,235,0.12);
  border:1px solid rgba(37,99,235,0.30);
  border-radius:40px;padding:5px 14px;
  font-size:0.75rem;font-weight:600;color:var(--sky);
  margin-bottom:28px;letter-spacing:0.04em;text-transform:uppercase;
}
.hero h1{
  font-size:clamp(2.2rem,5vw,3.6rem);
  font-weight:900;
  color:#fff;
  line-height:1.12;
  letter-spacing:-1.5px;
  margin-bottom:22px;
}
.hero h1 span{
  background:linear-gradient(90deg,var(--blue),var(--sky));
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;
}
.hero-sub{
  font-size:1.1rem;color:var(--muted);
  max-width:580px;margin:0 auto 40px;line-height:1.7;
}
.hero-cta{
  display:flex;flex-wrap:wrap;justify-content:center;gap:12px;
  margin-bottom:20px;
}
.btn-primary{
  display:inline-flex;align-items:center;gap:6px;
  padding:14px 28px;border-radius:10px;font-size:0.96rem;font-weight:700;
  background:linear-gradient(135deg,var(--blue),var(--blue-dark));
  color:#fff;text-decoration:none;
  box-shadow:0 4px 20px rgba(37,99,235,0.35);
  transition:opacity 0.15s,transform 0.12s,box-shadow 0.15s;
}
.btn-primary:hover{
  opacity:0.92;transform:translateY(-2px);
  box-shadow:0 6px 28px rgba(37,99,235,0.45);color:#fff;
}
.btn-ghost{
  display:inline-flex;align-items:center;gap:6px;
  padding:14px 28px;border-radius:10px;font-size:0.96rem;font-weight:600;
  background:rgba(255,255,255,0.06);
  border:1px solid var(--border);
  color:var(--text);text-decoration:none;
  transition:background 0.15s,transform 0.12s;
}
.btn-ghost:hover{background:rgba(255,255,255,0.10);transform:translateY(-2px);color:#fff}
.hero-note{font-size:0.78rem;color:var(--dim)}
.hero-note b{color:var(--muted)}

/* Screenshot / mockup area */
.hero-screen{
  margin-top:60px;
  background:var(--navy2);
  border:1px solid var(--border);
  border-radius:16px;
  overflow:hidden;
  box-shadow:0 32px 80px rgba(0,0,0,0.5),0 0 0 1px rgba(255,255,255,0.04);
  max-width:900px;margin-left:auto;margin-right:auto;
  position:relative;
}
.screen-bar{
  background:#0F172A;
  padding:10px 16px;
  display:flex;align-items:center;gap:6px;
  border-bottom:1px solid var(--border);
}
.dot{width:10px;height:10px;border-radius:50%}
.dot-r{background:#EF4444}.dot-y{background:#F59E0B}.dot-g{background:#22C55E}
.screen-body{padding:28px 24px;display:flex;flex-direction:column;gap:16px}
.kpi-row{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.kpi{
  background:#0F172A;border-radius:10px;padding:16px 14px;
  border-top:3px solid var(--blue);
}
.kpi.sky{border-top-color:var(--sky)}
.kpi.teal{border-top-color:var(--teal)}
.kpi.amber{border-top-color:#D97706}
.kpi-val{font-size:1.5rem;font-weight:700;color:var(--text);margin-bottom:2px}
.kpi-lbl{font-size:0.65rem;text-transform:uppercase;letter-spacing:0.07em;color:var(--dim)}
.kpi-delta{font-size:0.72rem;color:#22C55E;margin-top:4px}
.chart-area{
  background:#0F172A;border-radius:10px;padding:20px;
  display:flex;align-items:flex-end;gap:6px;height:130px;
}
.bar{
  border-radius:4px 4px 0 0;flex:1;min-width:0;
  background:linear-gradient(to top,var(--blue),var(--sky));
  opacity:0.75;transition:opacity 0.2s;
}
.bar:hover{opacity:1}

/* ── LOGOS ── */
.logos-section{
  padding:28px 5%;
  border-top:1px solid var(--border);
  border-bottom:1px solid var(--border);
  background:rgba(255,255,255,0.015);
}
.logos-label{
  text-align:center;font-size:0.72rem;text-transform:uppercase;
  letter-spacing:0.1em;color:var(--dim);margin-bottom:20px;
}
.logos-row{
  display:flex;flex-wrap:wrap;justify-content:center;align-items:center;gap:32px 48px;
}
.logo-chip{
  font-size:0.85rem;font-weight:700;color:var(--dim);
  letter-spacing:0.02em;opacity:0.6;
}

/* ── FEATURES ── */
.section{padding:90px 5%;max-width:1140px;margin:0 auto}
.section-tag{
  display:inline-block;
  font-size:0.72rem;font-weight:700;text-transform:uppercase;letter-spacing:0.1em;
  color:var(--sky);margin-bottom:14px;
}
.section-title{
  font-size:clamp(1.6rem,3.5vw,2.4rem);font-weight:800;
  color:#fff;letter-spacing:-0.8px;line-height:1.2;
  margin-bottom:14px;max-width:600px;
}
.section-sub{font-size:0.95rem;color:var(--muted);max-width:520px;line-height:1.7}
.features-grid{
  display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));
  gap:20px;margin-top:48px;
}
.feature-card{
  background:var(--navy2);
  border:1px solid var(--border);
  border-radius:var(--radius);
  padding:28px 24px;
  transition:border-color 0.2s,transform 0.2s;
}
.feature-card:hover{border-color:rgba(37,99,235,0.4);transform:translateY(-3px)}
.feature-icon{
  width:44px;height:44px;border-radius:11px;
  background:rgba(37,99,235,0.15);border:1px solid rgba(37,99,235,0.20);
  display:flex;align-items:center;justify-content:center;
  font-size:1.25rem;margin-bottom:16px;
}
.feature-title{font-size:1rem;font-weight:700;color:#fff;margin-bottom:8px}
.feature-desc{font-size:0.84rem;color:var(--muted);line-height:1.65}

/* ── HOW IT WORKS ── */
.steps-section{
  padding:90px 5%;
  background:linear-gradient(180deg,transparent,rgba(37,99,235,0.04) 50%,transparent);
}
.steps-inner{max-width:900px;margin:0 auto}
.steps{display:grid;grid-template-columns:repeat(3,1fr);gap:32px;margin-top:48px}
.step{text-align:center;padding:0 16px}
.step-num{
  width:44px;height:44px;border-radius:50%;
  background:linear-gradient(135deg,var(--blue),var(--sky));
  color:#fff;font-size:1rem;font-weight:800;
  display:flex;align-items:center;justify-content:center;
  margin:0 auto 18px;
  box-shadow:0 4px 16px rgba(37,99,235,0.35);
}
.step-title{font-size:1rem;font-weight:700;color:#fff;margin-bottom:8px}
.step-desc{font-size:0.84rem;color:var(--muted);line-height:1.65}

/* ── PRICING ── */
.pricing-section{padding:90px 5%;max-width:1000px;margin:0 auto;text-align:center}
.pricing-grid{
  display:grid;grid-template-columns:repeat(3,1fr);gap:20px;
  margin-top:48px;text-align:left;
}
.pricing-card{
  background:var(--navy2);
  border:1px solid var(--border);
  border-radius:var(--radius);
  padding:28px 24px;
}
.pricing-card.featured{
  border-color:var(--blue);
  background:linear-gradient(135deg,rgba(37,99,235,0.12),rgba(14,165,233,0.06));
  position:relative;
}
.featured-badge{
  position:absolute;top:-11px;left:50%;transform:translateX(-50%);
  background:linear-gradient(90deg,var(--blue),var(--sky));
  color:#fff;font-size:0.65rem;font-weight:700;text-transform:uppercase;
  letter-spacing:0.08em;padding:3px 12px;border-radius:20px;white-space:nowrap;
}
.plan-name{font-size:0.8rem;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:var(--muted);margin-bottom:10px}
.plan-price{font-size:2rem;font-weight:800;color:#fff;line-height:1.1}
.plan-price span{font-size:0.85rem;font-weight:500;color:var(--muted)}
.plan-desc{font-size:0.8rem;color:var(--muted);margin:10px 0 20px}
.plan-features{list-style:none;display:flex;flex-direction:column;gap:8px;margin-bottom:24px}
.plan-features li{font-size:0.83rem;color:var(--muted);padding-left:18px;position:relative}
.plan-features li::before{content:"✓";position:absolute;left:0;color:var(--sky);font-weight:700}
.btn-plan{
  display:block;text-align:center;padding:11px;border-radius:8px;
  font-size:0.88rem;font-weight:700;text-decoration:none;
  transition:opacity 0.15s,transform 0.1s;
}
.btn-plan-primary{background:linear-gradient(135deg,var(--blue),var(--blue-dark));color:#fff}
.btn-plan-ghost{background:rgba(255,255,255,0.07);border:1px solid var(--border);color:var(--text)}
.btn-plan:hover{opacity:0.88;transform:translateY(-1px)}

/* ── TESTIMONIALS ── */
.testimonials-section{padding:90px 5%;max-width:1140px;margin:0 auto}
.testimonials-grid{
  display:grid;grid-template-columns:repeat(3,1fr);gap:20px;margin-top:48px;
}
.testimonial{
  background:var(--navy2);border:1px solid var(--border);
  border-radius:var(--radius);padding:26px 22px;
}
.testimonial-text{font-size:0.9rem;color:var(--muted);line-height:1.7;margin-bottom:20px}
.testimonial-text::before{content:open-quote;font-size:1.4rem;color:var(--blue);line-height:0;vertical-align:-0.4em;margin-right:3px}
.testimonial-author{display:flex;align-items:center;gap:10px}
.author-avatar{
  width:36px;height:36px;border-radius:50%;
  background:linear-gradient(135deg,var(--blue),var(--sky));
  display:flex;align-items:center;justify-content:center;
  font-size:0.75rem;font-weight:700;color:#fff;flex-shrink:0;
}
.author-name{font-size:0.84rem;font-weight:600;color:#fff}
.author-role{font-size:0.72rem;color:var(--dim)}

/* ── CTA BANNER ── */
.cta-section{
  padding:90px 5%;text-align:center;
  background:linear-gradient(135deg,rgba(37,99,235,0.12),rgba(14,165,233,0.06));
  border-top:1px solid var(--border);border-bottom:1px solid var(--border);
  margin:40px 0;
}
.cta-section h2{
  font-size:clamp(1.6rem,3.5vw,2.4rem);font-weight:800;
  color:#fff;letter-spacing:-0.8px;margin-bottom:14px;
}
.cta-section p{font-size:1rem;color:var(--muted);margin-bottom:36px}

/* ── FOOTER ── */
footer{
  padding:40px 5% 32px;
  border-top:1px solid var(--border);
}
.footer-inner{
  max-width:1140px;margin:0 auto;
  display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:20px;
}
.footer-brand{display:flex;align-items:center;gap:8px;font-weight:700;color:var(--muted);font-size:0.88rem;text-decoration:none}
.footer-links{display:flex;flex-wrap:wrap;gap:20px}
.footer-links a{font-size:0.78rem;color:var(--dim);text-decoration:none;transition:color 0.15s}
.footer-links a:hover{color:var(--muted)}
.footer-copy{font-size:0.75rem;color:var(--dim);width:100%}

/* ── RESPONSIVE ── */
@media(max-width:768px){
  .kpi-row{grid-template-columns:repeat(2,1fr)}
  .features-grid,.steps,.pricing-grid,.testimonials-grid{grid-template-columns:1fr}
  .nav-links{display:none}
}
</style>
</head>
<body>

<!-- NAV -->
<nav>
  <a href="/landing" class="nav-brand">
    <div class="nav-logo">📊</div>
    InsightHub
  </a>
  <div class="nav-links">
    <a href="#features">Features</a>
    <a href="#how-it-works">How it works</a>
    <a href="#pricing">Pricing</a>
    <a href="/login">Sign in</a>
  </div>
  <a href="/signup" class="btn-nav">Start free trial →</a>
</nav>

<!-- HERO -->
<section class="hero">
  <div class="hero-glow"></div>
  <div class="hero-content">
    <div class="hero-badge">✦ Now with Year-over-Year Analysis</div>
    <h1>Analytics that actually<br><span>grow your business</span></h1>
    <p class="hero-sub">
      InsightHub gives your team live dashboards, automated reports, and deep revenue
      insights — without the complexity of enterprise BI tools.
    </p>
    <div class="hero-cta">
      <a href="/signup" class="btn-primary">Start 14-day free trial →</a>
      <a href="/pricing" class="btn-ghost">View pricing</a>
    </div>
    <p class="hero-note"><b>No credit card required.</b> Free for 14 days, then from $39/month.</p>

    <!-- Dashboard mockup -->
    <div class="hero-screen">
      <div class="screen-bar">
        <div class="dot dot-r"></div>
        <div class="dot dot-y"></div>
        <div class="dot dot-g"></div>
      </div>
      <div class="screen-body">
        <div class="kpi-row">
          <div class="kpi">
            <div class="kpi-val">$148.2K</div>
            <div class="kpi-lbl">Total Revenue</div>
            <div class="kpi-delta">↑ 12.4% vs last month</div>
          </div>
          <div class="kpi sky">
            <div class="kpi-val">2,841</div>
            <div class="kpi-lbl">Transactions</div>
            <div class="kpi-delta">↑ 8.1% vs last month</div>
          </div>
          <div class="kpi teal">
            <div class="kpi-val">$52.17</div>
            <div class="kpi-lbl">Avg. Order Value</div>
            <div class="kpi-delta">↑ 3.9% vs last month</div>
          </div>
          <div class="kpi amber">
            <div class="kpi-val">$31.4K</div>
            <div class="kpi-lbl">Total Costs</div>
            <div class="kpi-delta" style="color:#F59E0B">↑ 2.1% vs last month</div>
          </div>
        </div>
        <div class="chart-area">
          <div class="bar" style="height:42%"></div>
          <div class="bar" style="height:58%"></div>
          <div class="bar" style="height:52%"></div>
          <div class="bar" style="height:67%"></div>
          <div class="bar" style="height:74%"></div>
          <div class="bar" style="height:61%"></div>
          <div class="bar" style="height:83%"></div>
          <div class="bar" style="height:71%"></div>
          <div class="bar" style="height:90%"></div>
          <div class="bar" style="height:78%"></div>
          <div class="bar" style="height:95%"></div>
          <div class="bar" style="height:100%"></div>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- LOGOS -->
<div class="logos-section">
  <p class="logos-label">Trusted by growing businesses across industries</p>
  <div class="logos-row">
    <span class="logo-chip">Retail &amp; E-commerce</span>
    <span class="logo-chip">Healthcare &amp; Pharmacy</span>
    <span class="logo-chip">Food &amp; Beverage</span>
    <span class="logo-chip">Professional Services</span>
    <span class="logo-chip">Wholesale &amp; Distribution</span>
  </div>
</div>

<!-- FEATURES -->
<div id="features" style="padding-top:20px">
<div class="section">
  <div class="section-tag">Features</div>
  <h2 class="section-title">Everything you need to understand your numbers</h2>
  <p class="section-sub">Purpose-built for teams that want answers, not more spreadsheets.</p>

  <div class="features-grid">
    <div class="feature-card">
      <div class="feature-icon">📈</div>
      <div class="feature-title">Live Revenue Dashboard</div>
      <div class="feature-desc">Track daily, monthly, and yearly revenue in real time. Filter by branch, product, or date range with one click.</div>
    </div>
    <div class="feature-card">
      <div class="feature-icon">🔄</div>
      <div class="feature-title">Year-over-Year Comparison</div>
      <div class="feature-desc">Instantly see how this year stacks up against last year, month by month. Spot seasonal trends and growth patterns automatically.</div>
    </div>
    <div class="feature-card">
      <div class="feature-icon">📧</div>
      <div class="feature-title">Automated Weekly Reports</div>
      <div class="feature-desc">Receive a clean business summary in your inbox every Monday morning — no setup required beyond your email address.</div>
    </div>
    <div class="feature-card">
      <div class="feature-icon">🏪</div>
      <div class="feature-title">Multi-Branch Analytics</div>
      <div class="feature-desc">Compare performance across all your locations in a single view. Know which branch is leading and which needs attention.</div>
    </div>
    <div class="feature-card">
      <div class="feature-icon">💳</div>
      <div class="feature-title">Cash vs. Credit Tracking</div>
      <div class="feature-desc">Break down revenue by payment method. Understand your cash flow and outstanding credit at a glance.</div>
    </div>
    <div class="feature-card">
      <div class="feature-icon">📦</div>
      <div class="feature-title">Inventory Intelligence</div>
      <div class="feature-desc">Track stock levels, expiry dates, and purchase costs. Get alerts before you run out of high-demand items.</div>
    </div>
    <div class="feature-card">
      <div class="feature-icon">🧾</div>
      <div class="feature-title">GST &amp; Tax Reports</div>
      <div class="feature-desc">One-click GST summaries ready for your accountant. Export as PDF or Excel in seconds.</div>
    </div>
    <div class="feature-card">
      <div class="feature-icon">🔐</div>
      <div class="feature-title">Role-Based Access</div>
      <div class="feature-desc">Give staff members exactly the access they need — nothing more. Full audit trail on every action.</div>
    </div>
    <div class="feature-card">
      <div class="feature-icon">🌍</div>
      <div class="feature-title">Multi-Currency &amp; Multi-Region</div>
      <div class="feature-desc">Works out of the box for USD, GBP, INR, AED, and 20+ more currencies. Your data, your format.</div>
    </div>
  </div>
</div>
</div>

<!-- HOW IT WORKS -->
<div id="how-it-works" class="steps-section">
  <div class="steps-inner" style="text-align:center">
    <div class="section-tag" style="display:block;margin-bottom:14px">How it works</div>
    <h2 class="section-title" style="margin:0 auto 14px">Up and running in under 10 minutes</h2>
    <p class="section-sub" style="margin:0 auto">No technical setup. No data engineers. Just your business data, organized.</p>

    <div class="steps">
      <div class="step">
        <div class="step-num">1</div>
        <div class="step-title">Create your account</div>
        <div class="step-desc">Sign up in 60 seconds. Choose your business type and we'll configure the right dashboard for you.</div>
      </div>
      <div class="step">
        <div class="step-num">2</div>
        <div class="step-title">Upload your data</div>
        <div class="step-desc">Drop in your sales file (CSV or Excel) and InsightHub instantly processes and visualizes it.</div>
      </div>
      <div class="step">
        <div class="step-num">3</div>
        <div class="step-title">Get insights, not data</div>
        <div class="step-desc">Explore interactive charts, share dashboards with your team, and receive automated reports every week.</div>
      </div>
    </div>
  </div>
</div>

<!-- PRICING -->
<div id="pricing">
<div class="pricing-section">
  <div class="section-tag">Pricing</div>
  <h2 class="section-title" style="margin:0 auto 14px;max-width:100%">Simple, transparent pricing</h2>
  <p class="section-sub" style="margin:0 auto">Start free. Scale as you grow. Cancel any time.</p>

  <div class="pricing-grid">
    <div class="pricing-card">
      <div class="plan-name">Starter</div>
      <div class="plan-price">$39 <span>/ month</span></div>
      <div class="plan-desc">For small businesses just getting started with analytics.</div>
      <ul class="plan-features">
        <li>Up to 2 users</li>
        <li>1 branch / location</li>
        <li>Revenue &amp; cost dashboards</li>
        <li>Weekly email reports</li>
        <li>CSV &amp; Excel export</li>
        <li>6 months data history</li>
        <li>Email support</li>
      </ul>
      <a href="/signup" class="btn-plan btn-plan-ghost">Start free trial</a>
    </div>
    <div class="pricing-card featured">
      <div class="featured-badge">Most popular</div>
      <div class="plan-name">Growth</div>
      <div class="plan-price">$99 <span>/ month</span></div>
      <div class="plan-desc">For growing teams with multiple locations and advanced reporting needs.</div>
      <ul class="plan-features">
        <li>Up to 10 users</li>
        <li>Unlimited branches</li>
        <li>All Starter features</li>
        <li>Year-over-Year comparison</li>
        <li>GST / tax reports</li>
        <li>Inventory &amp; expiry tracking</li>
        <li>Custom date ranges</li>
        <li>Unlimited data history</li>
        <li>Priority support</li>
      </ul>
      <a href="/signup" class="btn-plan btn-plan-primary">Start free trial →</a>
    </div>
    <div class="pricing-card">
      <div class="plan-name">Enterprise</div>
      <div class="plan-price">Custom</div>
      <div class="plan-desc">For large organisations with complex reporting and compliance requirements.</div>
      <ul class="plan-features">
        <li>Unlimited users</li>
        <li>Unlimited branches</li>
        <li>All Growth features</li>
        <li>Custom integrations</li>
        <li>SSO &amp; MFA enforcement</li>
        <li>Dedicated account manager</li>
        <li>SLA guarantee</li>
        <li>Custom data retention</li>
        <li>On-premise option</li>
      </ul>
      <a href="mailto:hello@insighthub.io" class="btn-plan btn-plan-ghost">Contact sales</a>
    </div>
  </div>
</div>
</div>

<!-- TESTIMONIALS -->
<div class="testimonials-section">
  <div class="section-tag">Customer stories</div>
  <h2 class="section-title">Businesses that moved from guesswork to clarity</h2>

  <div class="testimonials-grid">
    <div class="testimonial">
      <p class="testimonial-text">We used to spend three hours every Monday pulling reports from three different systems. Now InsightHub emails it to us automatically. That time goes back into serving customers.</p>
      <div class="testimonial-author">
        <div class="author-avatar">RK</div>
        <div>
          <div class="author-name">Ravi K.</div>
          <div class="author-role">Owner, RetailPlus — 4 locations</div>
        </div>
      </div>
    </div>
    <div class="testimonial">
      <p class="testimonial-text">The year-over-year chart alone justified the subscription. I can finally walk into our board meeting and show exactly how we grew — month by month, branch by branch.</p>
      <div class="testimonial-author">
        <div class="author-avatar">SL</div>
        <div>
          <div class="author-name">Sarah L.</div>
          <div class="author-role">Finance Director, Wellness Group</div>
        </div>
      </div>
    </div>
    <div class="testimonial">
      <p class="testimonial-text">Setup was shockingly simple. Uploaded our Excel file on Monday, had insights by Monday afternoon. Our accountant loves the GST export.</p>
      <div class="testimonial-author">
        <div class="author-avatar">AM</div>
        <div>
          <div class="author-name">Ahmed M.</div>
          <div class="author-role">Operations Manager, QuickMart</div>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- CTA BANNER -->
<div class="cta-section">
  <h2>Ready to see your business clearly?</h2>
  <p>Join hundreds of businesses using InsightHub to make faster, smarter decisions.</p>
  <div style="display:flex;justify-content:center;gap:12px;flex-wrap:wrap">
    <a href="/signup" class="btn-primary">Start your free 14-day trial →</a>
    <a href="/pricing" class="btn-ghost">See all plans</a>
  </div>
  <p style="margin-top:18px;font-size:0.78rem;color:var(--dim)">No credit card required &nbsp;·&nbsp; Cancel any time &nbsp;·&nbsp; Setup in under 10 minutes</p>
</div>

<!-- FOOTER -->
<footer>
  <div class="footer-inner">
    <a href="/landing" class="footer-brand">
      <div style="width:26px;height:26px;border-radius:6px;background:linear-gradient(135deg,#2563EB,#0EA5E9);display:flex;align-items:center;justify-content:center;font-size:13px">📊</div>
      InsightHub
    </a>
    <div class="footer-links">
      <a href="#features">Features</a>
      <a href="#pricing">Pricing</a>
      <a href="/signup">Sign up</a>
      <a href="/login">Sign in</a>
      <a href="/privacy">Privacy Policy</a>
      <a href="/data-deletion">Delete My Data</a>
      <a href="mailto:hello@insighthub.io">Contact</a>
    </div>
    <p class="footer-copy">© 2026 InsightHub Technologies, Inc. All rights reserved.</p>
  </div>
</footer>

<script src="/cookie-consent.js" defer></script>
</body>
</html>"""


def render_landing():
    """Return the full landing page HTML."""
    return LANDING_HTML
