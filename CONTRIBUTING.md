# دليل المساهمة والتطوير (CONTRIBUTING.md) — ARX-cutShor

نرحب بأي تحسينات أو مساهمات تقنية في مشروع **ARX-cutShor**.

---

## 1. القواعد المعمارية الإلزامية

عند إضافة ميزة جديدة أو تعديل وحدة حالية، التزم بالفصل التام بين الوحدات العشر:
1. واجهة الإدارة (`arx_cutshor/web_app.py`).
2. إعدادات الخدمات (`arx_cutshor/config.py`).
3. مراقبة Google Drive (`arx_cutshor/gdrive.py`).
4. معالجة الفيديو (`arx_cutshor/video_engine.py`).
5. إنشاء بيانات النشر (`arx_cutshor/metadata.py`).
6. إدارة جدول النشر (`arx_cutshor/scheduler.py`).
7. الرفع إلى YouTube (`arx_cutshor/youtube.py`).
8. قاعدة البيانات (`arx_cutshor/database.py`).
9. التسجيل والأمان والمراقبة (`arx_cutshor/security.py`).
10. الاختبارات (`tests/`).

---

## 2. ضوابط الأمان وحقوق النشر

* **ممنوع** رفع أي ملفات أسرار (`client_secret.json`, `token.json`, `.env`) أو ملفات فيديو كبيرة إلى مستودع Git.
* **ممنوع** إضافة أي فلاتر أو آليات لتشويه الصوت أو السرعة أو الألوان بقصد خداع أنظمة حقوق النشر (`Content ID`).
* تأكد من نجاح جميع الاختبارات قبل إرسال أي Pull Request:
  ```bash
  .venv/bin/pytest -v
  ```
