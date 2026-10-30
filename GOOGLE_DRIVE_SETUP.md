# دليل إعداد وربط Google Drive (GOOGLE_DRIVE_SETUP.md) — ARX-cutShor

يوضح هذا الدليل كيفية ربط حسابك في Google Drive بنظام **ARX-cutShor** باستخدام واجهة **Google Drive API v3** الرسمية وبأقل صلاحيات ممكنة (`Least Privilege`).

---

## 1. ملاحظة معمارية مهمة حول المصادقة

* لا تفترض أن حساب الخدمة (`Service Account`) يستطيع الوصول إلى ملفات Google Drive الشخصية تلقائياً إلا إذا تمت مشاركة المجلد معه صراحةً، كما أن حسابات الخدمة قد تواجه قيود حصة تخزين عند إنشاء ملفات.
* لذلك يدعم النظام **OAuth 2.0 (User Consent + Refresh Token)** مما يتيح لك تسجيل الدخول مرة واحدة محلياً، ثم يستخدم النظام `refresh_token` تلقائياً في المهام الدورية دون تدخل يدوي.

---

## 2. هيكل المجلدات المقترح داخل Google Drive

أنشئ مجلداً رئيسياً باسم `ARX-cutShor` داخل Google Drive، أو دع النظام ينشئ المجلدات الفرعية التالية داخله:

```text
ARX-cutShor/
├── input/        # ضع الفيديوهات والحلقات المصدر هنا
├── processing/   # الملفات الجاري معالجتها
├── ready/        # المقاطع الجاهزة للنشر
├── published/    # سجل أو نسخ اختيارية من المخرجات المنشورة
├── failed/       # ملفات تحتاج مراجعة
├── assets/       # الشعارات والمقدمات والخواتيم (logo, intro, outro, music)
└── reports/      # تقارير التشغيل
```

> **ملاحظة**: لا ينقل النظام ملفات المصدر أو يحذفها افتراضياً، بل يعتمد على معرف الملف الثابت (`drive_file_id`) وبصمته (`md5Checksum`) لتتبع حالته في قاعدة البيانات.

---

## 3. خطوات تفعيل Google Drive API وإنشاء بيانات الاعتماد

1. افتح [Google Cloud Console](https://console.cloud.google.com/).
2. أنشئ مشروعاً جديداً (أو اختر مشروعاً حالياً).
3. انتقل إلى **APIs & Services > Library** وابحث عن **Google Drive API** ثم اضغط **Enable**.
4. انتقل إلى **APIs & Services > OAuth consent screen**:
   * اختر **External** (أو Internal لحسابات Workspace).
   * أضف بريدك الإلكتروني ضمن **Test users**.
   * أضف النطاقين (Scopes) التاليين فقط:
     * `https://www.googleapis.com/auth/drive.readonly`
     * `https://www.googleapis.com/auth/drive.file`
5. انتقل إلى **APIs & Services > Credentials**:
   * اضغط **Create Credentials > OAuth client ID**.
   * اختر **Desktop app**.
   * نزّل ملف JSON واحفظه محلياً في المسار:
     `credentials/gdrive_client_secret.json`
   * (هذا المسار مضاف مسبقاً إلى `.gitignore` ولن يتم رفعه إلى GitHub أبداً).

---

## 4. استخراج معرف المجلد (`GDRIVE_INPUT_FOLDER_ID`)

1. افتح مجلد `input` في متصفحك داخل Google Drive.
2. انسخ المعرف الموجود في نهاية الرابط:
   `https://drive.google.com/drive/folders/<THIS_IS_THE_FOLDER_ID>`
3. ضع المعرف في ملف `.env`:
   ```env
   GDRIVE_ENABLED=true
   GDRIVE_INPUT_FOLDER_ID=ضع_المعرف_هنا
   ```

---

## 5. تسجيل الدخول الأول وتوليد `gdrive_token.json`

نفّذ الأمر التالي لمرة واحدة على جهازك لفتح المتصفح والموافقة على الصلاحيات:

```bash
.venv/bin/python -c "from arx_cutshor.config import load_config; from arx_cutshor.pipeline import PipelineOrchestrator; PipelineOrchestrator(load_config()).gdrive.authenticate(interactive=True)"
```

سيتم حفظ الرمز في `credentials/gdrive_token.json`، ويمكنك بعدها تشغيل `discover` و `run-cycle` آلياً بالكامل.
