# دليل التثبيت (INSTALLATION.md) — ARX-cutShor

يوضح هذا الدليل خطوات تثبيت وتشغيل نظام **ARX-cutShor** على macOS وLinux وWindows.

---

## 1. المتطلبات الأساسية

1. **Python 3.11 أو أحدث** (تم الفحص والاختبار على `Python 3.14.6`).
2. **Git** لإدارة الشيفرة المصدرية.
3. **FFmpeg**:
   * يتضمن المشروع حزمة `imageio-ffmpeg` ضمن `requirements.txt` والتي توفر ملفاً تنفيذياً جاهزاً لـ `ffmpeg` تلقائياً دون الحاجة لتثبيت خارجي معقد.
   * كما يمكنك اختيارياً تثبيت `ffmpeg` و `ffprobe` على مستوى النظام:
     * **macOS (Homebrew)**: `brew install ffmpeg`
     * **Ubuntu / Debian**: `sudo apt-get update && sudo apt-get install -y ffmpeg`

---

## 2. خطوات التثبيت

### الخطوة 1: استنساخ المستودع أو الدخول إلى مجلد المشروع

```bash
cd /Users/abdullah/Desktop/ARX-cutShor
```

### الخطوة 2: إنشاء البيئة الافتراضية (Virtual Environment)

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### الخطوة 3: تثبيت الحزم البرمجية

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### الخطوة 4: تهيئة ملف الإعدادات البيئية

```bash
cp .env.example .env
```

افتح ملف `.env` وعدّل القيم المناسبة لبيئتك (يأتي الوضع الافتراضي مضبوطاً على `DRY_RUN=true` و `PUBLISHING_MODE=testing` لضمان الأمان الكامل عند التشغيل الأول).

---

## 3. التحقق من نجاح التثبيت

نفّذ أمر الفحص الذاتي (`audit`) للتأكد من جاهزية قاعدة البيانات وFFmpeg:

```bash
.venv/bin/python -m arx_cutshor.cli audit
```

ونفّذ الاختبارات الآلية للتحقق من سلامة جميع الوحدات:

```bash
.venv/bin/pytest -v
```

