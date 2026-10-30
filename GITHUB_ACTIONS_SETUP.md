# دليل التشغيل الآلي عبر GitHub Actions (GITHUB_ACTIONS_SETUP.md) — ARX-cutShor

يوضح هذا الدليل كيفية عمل مهام `GitHub Actions` المقسمة في المشروع، والفرق المعماري بين المهام الدورية والخادم الدائم (`24/7 Server`).

---

## 1. قائمة الـ Workflows المنفصلة في المشروع

تم فصل المهام إلى 6 ملفات مستقلة داخل `.github/workflows/` لسهولة التصحيح والمراقبة:

1. `tests.yml`: تشغيل حزمة الاختبارات الآلية (`pytest`) عند كل Push أو Pull Request.
2. `config_check.yml`: فحص أمان المستودع والتأكد من عدم رفع أي ملفات أسرار أو فيديوهات، وتشغيل `cli audit`.
3. `discover.yml`: فحص مجلد Google Drive دورياً لاكتشاف الفيديوهات الجديدة.
4. `process_queue.yml`: معالجة قائمة انتظار صغيرة بحدود زمنية آمنة (`timeout-minutes: 30`) مع قفل تزامن (`concurrency`) يمنع تشغيل مهمتين متعارضتين في نفس الوقت.
5. `status_report.yml`: إصدار تقرير حالة الدوري.
6. `maintenance.yml`: استعادة العمليات المتوقفة وتنظيف الملفات المؤقتة.

---

## 2. إعداد GitHub Secrets والمتغيرات

من إعدادات مستودعك على GitHub انتقل إلى **Settings > Secrets and variables > Actions**:

### الأسرار (Repository Secrets):
* `GDRIVE_TOKEN_JSON`: محتوى ملف `credentials/gdrive_token.json` بعد تسجيل الدخول الأول محلياً.
* `YOUTUBE_TOKEN_JSON`: محتوى ملف `credentials/youtube_token.json` بعد تسجيل الدخول الأول محلياً.
* `GDRIVE_INPUT_FOLDER_ID`: معرف مجلد `input` الثابت في Google Drive.

### المتغيرات (Repository Variables):
* `DRY_RUN`: `true` (أو `false` بعد انتهاء الاختبار).
* `PUBLISHING_MODE`: `testing` (أو `scheduled`).
* `ALLOW_PUBLIC_PUBLISHING`: `false` (لا تجعله `true` إلا بعد موافقتك الصريحة).
* `GDRIVE_ENABLED`: `true`
* `YOUTUBE_ENABLED`: `true`

---

## 3. الفرق المعماري بين GitHub Actions والتشغيل الدائم 24/7

* **GitHub Actions خدمة مهام دورية وليست خادماً دائماً**:
  * قد تتأخر مهام `schedule (cron)` في GitHub Actions من بضع دقائق إلى 15–30 دقيقة في أوقات الذروة.
  * بيئة التشغيل في كل مهمة (`Runner`) هي بيئة مؤقتة (`Ephemeral VM`) تُمسح فور انتهاء المهمة؛ لذلك لا يتم الاعتماد على ملف SQLite المحلي داخل Runner كقاعدة بيانات دائمة، ولا يتم عمل Commit لقاعدة البيانات مع كل مهمة.
  * بدلاً من ذلك، يعتمد النظام على:
    1. **Google Drive** كمصدر دائم للملفات والتقارير.
    2. **YouTube API State Reconciliation** للتحقق من الفيديوهات المرفوعة مسبقاً ومنع التكرار.
* **الحدود المجانية لـ GitHub Actions**:
  * المستودعات الخاصة (`Private`) تحصل على `2,000` دقيقة مجانية شهرياً في الخطة المجانية.
  * المستودعات العامة (`Public`) تحصل على دقائق مجانية، ولكن لا تحفظ أي أسرار داخل الشيفرة أبداً.
* **متى تحتاج إلى جهاز دائم التشغيل أو VPS؟**
  * إذا أردت تشغيل واجهة الويب الإدارية (`serve`) بشكل مستمر أو الاحتفاظ بقاعدة بيانات SQLite محلية دائمة ومراقبة فورية بالثانية، فشغّل النظام على جهازك الشخصي أو خادم Linux صغير (`VPS`) عبر `cron` أو `systemd`.

