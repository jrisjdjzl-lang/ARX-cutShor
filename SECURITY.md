# سياسة الأمان والخصوصية (SECURITY.md) — ARX-cutShor

تم تصميم **ARX-cutShor** وفق مبدأ **الأمان أولاً (`Security by Design`)** وبأقل صلاحيات ممكنة (`Least Privilege`).

---

## 1. حماية الأسرار ورموز المصادقة

* لا يتم تخزين أي كلمات مرور أو مفاتيح API أو رموز OAuth (`access_token`, `refresh_token`) داخل قاعدة بيانات `SQLite` أو داخل مستودع Git.
* يحظر ملف `.gitignore` رفع أي ملفات أسرار (`*secret*.json`, `token*.json`, `.env`, `*.pem`, `*.key`).
* يتحقق Workflow الفحص الأمني (`config_check.yml`) تلقائياً عند كل Push من خلو المستودع من أي ملفات أسرار أو فيديوهات.
* يقوم مرشح السجلات (`SecretScrubbingFilter`) في وحدة `arx_cutshor/security.py` بحجب أي رموز سرية محتملة (`[REDACTED_SECRET]`) قبل كتابتها في ملفات السجل أو جدول `operation_logs`.

---

## 2. حماية نظام الملفات ومنع حقن الأوامر (Command Injection & Path Traversal)

* لا يتم تمرير أسماء الملفات القادمة من Google Drive أو المستخدم إلى أوامر Shell نصية أبداً؛ جميع استدعاءات `FFmpeg` تتم عبر مصفوفات معاملات معزولة (`subprocess.run([...], shell=False)`).
* تخضع جميع أسماء الملفات لدالة `sanitize_filename()` التي تزيل المحارف الخطرة وتتعامل بأمان مع المسافات والأسماء العربية.
* تمنع دالة `safe_resolve_path()` أي محاولة للخروج من مجلد المعالجة المسموح به (`Path Traversal Protection`).

---

## 3. حماية لوحة التحكم المحلية

* ترتبط واجهة الإدارة الافتراضية بالعنوان المحلي فقط (`127.0.0.1`) ولا تُعرض على الإنترنت العام.
* يتحقق الخادم من مصدر الطلب ومن رمز الحماية `ADMIN_ACCESS_TOKEN` لأي طلب خارج `localhost`.

---

## 4. خطوات إلغاء صلاحيات التطبيقات عند الحاجة (Revoking OAuth Access)

إذا رغبت في أي وقت بإلغاء وصول التطبيق إلى حسابك في Google Drive أو YouTube فوراً:

1. افتح صفحة [أمان حساب Google — التطبيقات والخدمات التابعة لجهات خارجية](https://myaccount.google.com/permissions).
2. اختر اسم تطبيق المشروع الخاص بك واضغط **إزالة جميع عمليات الربط (Delete all connections)**.
3. احذف ملفات الرموز المحلية:
   ```bash
   rm -f credentials/gdrive_token.json credentials/youtube_token.json
   ```
4. احذف الأسرار المقابلة من **GitHub Repository Secrets**.
