# دليل إعداد YouTube Data API v3 (YOUTUBE_API_SETUP.md) — ARX-cutShor

يوضح هذا الدليل كيفية ربط قناتك على يوتيوب بنظام **ARX-cutShor** لرفع وجدولة مقاطع **YouTube Shorts** بأمان.

---

## 1. القيود المهمة وأهلية القناة في YouTube Data API v3

قبل تفعيل النشر التلقائي، يجب معرفة القواعد الرسمية التالية من YouTube:

1. **قفل الفيديوهات الخاصة للمشاريع غير المعتمدة (Unverified API Projects)**:
   * أي مشروع جديد في Google Cloud لم يخضع لمراجعة التحقق الرسمية من Google (`Compliance Audit`) قد يقوم يوتيوب بقفل الفيديوهات المرفوعة عبره كـ `Private` إذا لم يكن التطبيق في وضع الإنتاج المعتمد.
2. **الحصة اليومية المجانية (Quota Limits)**:
   * الحصة المجانية الافتراضية هي `10,000` وحدة يومياً.
   * كل عملية رفع فيديو (`videos.insert`) تستهلك `1,600` وحدة، أي بحد أقصى **6 مقاطع يومياً** مجاناً.
3. **أهلية القناة للجدولة (`publishAt`)**:
   * لجدولة فيديو عبر API يجب إرسال `status.privacyStatus = "private"` مع حقل `status.publishAt` بصيغة `ISO 8601 UTC`.
   * يوصى بتفعيل **الميزات المتقدمة (Advanced Features)** في استوديو يوتيوب للقناة قبل استخدام الجدولة التلقائية.

---

## 2. خطوات تفعيل YouTube Data API v3

1. افتح [Google Cloud Console](https://console.cloud.google.com/).
2. من **APIs & Services > Library** ابحث عن **YouTube Data API v3** واضغط **Enable**.
3. في **OAuth consent screen** أضف النطاقين التاليين:
   * `https://www.googleapis.com/auth/youtube.upload`
   * `https://www.googleapis.com/auth/youtube.readonly`
4. من **Credentials** أنشئ **OAuth client ID** من نوع **Desktop app** واحفظ الملف في:
   `credentials/youtube_client_secret.json`

---

## 3. تسجيل الدخول الأول وتوليد `youtube_token.json`

شغّل الأمر التالي لمرة واحدة محلياً لتسجيل الدخول بحساب القناة وحفظ رمز التحديث (`Refresh Token`):

```bash
.venv/bin/python -c "from arx_cutshor.config import load_config; from arx_cutshor.pipeline import PipelineOrchestrator; PipelineOrchestrator(load_config()).youtube.authenticate(interactive=True)"
```

---

## 4. مراحل الانتقال الآمن من الاختبار إلى النشر المجدول

1. **المرحلة الأولى (`DRY_RUN=true`)**:
   * اختبار تقسيم الفيديو والقالب محلياً دون أي اتصال بيوتيوب.
2. **المرحلة الثانية (`DRY_RUN=false`, `PUBLISHING_MODE=testing`, `ALLOW_PUBLIC_PUBLISHING=false`)**:
   * رفع فيديو اختباري كـ `private` أو `unlisted` للتحقق من المظهر والصوت والعنوان داخل استوديو يوتيوب.
3. **المرحلة الثالثة (`PUBLISHING_MODE=scheduled`, `ALLOW_PUBLIC_PUBLISHING=true`)**:
   * لا تقم بتفعيل هذا الوضع إلا بعد التأكد من نجاح المرحلة الثانية وموافقتك الصريحة على بدء النشر المجدول العام.

