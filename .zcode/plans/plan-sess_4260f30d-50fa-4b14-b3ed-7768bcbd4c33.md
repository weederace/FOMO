# بهبود Movers + Alerts + رنگ‌بندی و آیکن جداول

## ۱. Movers: دو ستون لینک FOMO + GMGN (انتخاب کاربر)
**`scripts/launcher.py`:**
- در `_render_movers` ردیف‌ها غنی‌تر شوند: `trader_id` از پاسخ rankings، و `wallet` با join از `self.dashboard_rows` (کلید id) اضافه شود.
- در `_build_movers` دو ستون جدید با متن "↗" و `click=True` اضافه شود: `fomo` (رنگ ACCENT) و `gmgn` (رنگ PURPLE، وقتی wallet نباشد "—").
- هندلر `_movers_click`: ستون fomo → `webbrowser.open(f"https://fomo.family/profile/{handle}")` (الگوی موجود leaderboard). ستون gmgn → اگر wallet داشت `https://gmgn.ai/sol/wallet/{wallet}` (chain از wallet format استنتاج: شروع با 0x → base،否则 sol)؛ اگر نه → کپی لاگ «no wallet».
- هندلر جدول leaderboard هم به همین سبک گسترش می‌یابد (ستون gmgn اضافه شود) تا همه‌جا یکدست باشد.

## ۲. Alerts: پاپ‌آپ hover + باز کردن در سایت
**داده:** در `_render_alerts` فیلدهای `_chain`, `_address`, `_symbol` از payload در ردیف ذخیره شوند (الگوی `_full` موجود).
**پاپ‌آپ hover (Toplevel بدون تزئینات، شبیه Tooltip موجود ولی غنی‌تر):**
- کلاس جدید `DetailPopup` (مبتنی بر `Toplevel` + `overrideredirect`) که وقتی موس روی ردیف alert مکث کند (مهلت ~600ms، الگوی Tooltip در launcher.py:199-230) پنلی نشان دهد:
  - عنوان: آیکن نوع الرت + symbol + chain با ایموجی زنجیره (◎ ◆ ▲ Ξ — map موجود)
  - آدرس کانترکت کامل با دکمه Copy (کلیپ‌بورد موجود)
  - تغییر ۵ دقیقه (٪ رنگ سبز/قرمز) و حجم 5m/1h — منبع داده: از کش رادار `/gmgn/*` که لانچر هر ۶۰ ثانیه می‌گیرد، جستجو با address. اگر در کش نبود "—" نشان داده شود.
  - خط پیام الرت
- بعد از کلیک روی ستون `open` (↗) → `webbrowser.open(f"https://gmgn.ai/{chain}/token/{address}")`؛ ستون آدرس هم کلیک = کپی.
- برای الرت‌های نهنگی (بدون address) پاپ‌آپ ساده: متن کامل + مبلغ.
- آیکن‌های نوع الرت: 🐋 NEW_SMART_WHALE، 🚀 EMERGING_WHALE، 🔥 GMGN_TRENDING، 🆕 GMGN_NEW_TOKEN، 🎓 GMGN_NEAR_GRADUATION، 🔍 GMGN_HOT_SEARCH، 💸 FOMO_BUY، 💰 FOMO_SELL (متن-سازگار با Tk).

## ۳. رنگ‌بندی، سایز و آیکن جداول
**در DataTable (launcher.py):**
- زبرا ملایم‌تر: حتی=PANEL2، فرد="#131722"؛ hover کمی روشن‌تر (#1e2534 موجود).
- pill ها: پس‌زمینه‌های DIM موجود (GREEN_DIM/RED_DIM/AMBER_DIM/PURPLE_DIM) — قبلاً هست؛ فقط اطمینان از padding متن کافی و `min width = len*7+20`.
- ستون‌ها سایز متناسب: عرض ستون‌های جدول movers/alerts/tokens/radar با توجه به محتوا تنظیم شود (مثلاً handle بلندتر، value کوتاه‌تر). ستون `message` stretch باقی می‌ماند.
- آیکن‌های ستونی:
  - Movers: مثلث سبز/قرمز ▲▼ کنار score_growth (بر اساس علامت)
  - Alerts: آیکن نوع الرت در ستون type (جدول بالا)
  - Radar: ایموجی زنجیره در ستون chain (◎ ◆ ▲ Ξ)
  - Tokens: پیل وضعیت بازار با دات رنگی
  - Flow: آیکن ▲/▼ کنار increase_5m/increase_1h در جدول volume
- فونت‌ها: سلول‌های عددی mono بمانند؛ عنوان بخش‌ها `(FONT_BOLD, 7)` بماند؛ فقط row_height از 30→32 برای تنفس بیشتر.

## ۴. تست و اعتبارسنجی
- رندر headless: سوییچ همه تب‌ها + hover alert (شبیه‌سازی `DetailPopup.show`) + کلیک movers (بدون وب) با متد مستقیم — همه PASS.
- `ruff check` + کل `pytest` (۸۴ تست فعلی نباید بشکند).
- اسکرین‌شات demo از تب Movers و Alerts برای تأیید بصری.
- ری‌استارت لانچر برای اعمال؛ هیچ تغییر backend لازم نیست (تمام داده‌های موردنیاز موجودند).