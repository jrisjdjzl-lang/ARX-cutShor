# دليل الإعدادات والتكوين (CONFIGURATION.md) — ARX-cutShor

يدير نظام **ARX-cutShor** إعداداته عبر متغيرات البيئة (`.env`) وجدول الإعدادات والقوالب في قاعدة بيانات `SQLite`.

---

## 1. أوضاع التشغيل والأمان

| المتغير | القيم المتاحة | الوصف |
|---|---|---|
| `APP_ENV` | `development`, `testing`, `production` | بيئة التشغيل الحالية. |
| `DRY_RUN` | `true` / `false` | عند تفعيله (`true`) تتم محاكاة الجدولة والرفع إلى يوتيوب دون أي نشر حقيقي أو استهلاك للحصة. |
| `PUBLISHING_MODE` | `testing` / `scheduled` | في وضع `testing` يتم إجبار جميع عمليات الرفع على `private` أو `unlisted` ومنع النشر العام. |
| `ALLOW_PUBLIC_PUBLISHING` | `true` / `false` | بوابة أمان صريحة؛ يجب أن تكون `true` للسماح بأي نشر عام (`public`). |
| `REQUIRE_RIGHTS_CONFIRMATION` | `true` / `false` | يفرض أن تكون حالة حقوق الفيديو `confirmed` قبل السماح بجدولته للنشر العام. |

---

## 2. إعدادات تقسيم ومعالجة الفيديو

| المتغير | القيمة الافتراضية | الوصف |
|---|---|---|
| `SPLIT_MODE` | `fixed` | وضع التقسيم: `fixed` (مدة ثابتة)، `scene` (مشاهد)، `manual` (يدوي)، `hybrid` (هجين). |
| `TARGET_CLIP_DURATION_SEC` | `55.0` | المدة المستهدفة لكل مقطع بالثواني. |
| `MIN_CLIP_DURATION_SEC` | `15.0` | الحد الأدنى لمدة المقطع لتجنب المقاطع القصيرة جداً. |
| `MAX_CLIP_DURATION_SEC` | `59.0` | الحد الأقصى لمدة المقطع بما يتوافق مع YouTube Shorts. |
| `SAFE_END_MARGIN_SEC` | `0.5` | هامش الأمان قبل نهاية المقطع. |
| `OUTPUT_WIDTH` / `OUTPUT_HEIGHT` | `1080` / `1920` | أبعاد الفيديو العمودية (`9:16`). |

---

## 3. إعدادات الهوية البصرية (Templates)

تُحفظ إعدادات القالب في جدول `templates` بصيغة JSON ويمكن تعديلها مباشرة من تبويب **القوالب والهوية (Templates)** في واجهة الويب:

* `logo_enabled`: تفعيل/تعطيل شعار القناة.
* `logo_path`: مسار ملف الشعار (`PNG` شفاف).
* `logo_position`: موضع الشعار (`top-right`, `top-left`, `bottom-right`, `bottom-left`).
* `intro_enabled` / `outro_enabled`: تفعيل أو تعطيل المقدمة والخاتمة.
* `subscribe_text_enabled` / `like_text_enabled`: تفعيل أو تعطيل عبارات الاشتراك والإعجاب.
* `font_color` / `font_size`: لون وحجم الخط العربي.

---

## 4. إعدادات المجدول (Scheduler)

* `TIMEZONE`: المنطقة الزمنية (مثل `Asia/Riyadh` أو `Asia/Damascus`).
* `DAILY_CLIP_LIMIT`: عدد المقاطع المسموح بنشرها يومياً (الحد الأقصى ضمن الحصة المجانية ليوتيوب هو `6` يومياً).
* `ALLOWED_PUBLISH_DAYS`: أيام الأسبوع المسموح بالنشر فيها (`0` = الإثنين .. `6` = الأحد).
* `PUBLISH_HOURS`: ساعات النشر المفضلة مفصولة بفواصل (مثال: `14:00,18:00,21:00`).
* `MIN_INTERVAL_MINUTES`: الحد الأدنى للفاصل الزمني بين مقطعين بالدقائق.

