# Test Email Examples for Email Response Agent

This file documents the sample emails sent to the test inbox for evaluating the agent's capabilities.
Each entry describes the email's purpose, what agent behavior it exercises, and the full Hebrew content as sent.

---

## How to Use

When testing the agent, ask it to:
- Find and summarize a specific email (by subject or sender)
- Draft a reply to one of the emails below
- Identify which emails are urgent or require action

Use the emails below to verify the agent handles different tones, intents, and structures correctly.

---

## Email Examples

---

### 1. Formal Meeting Scheduling

**Category:** Business / Coordination
**Tests:** Date extraction, scheduling intent detection, polite reply generation

**Subject:** קביעת פגישת עדכון פרויקט – רבעון 2

**Body:**
> היי,
>
> בהמשך לשיחתנו, אשמח אם נוכל לקבוע פגישה קצרה של חצי שעה כדי לעבור על היעדים של הרבעון הקרוב.
> יש לי זמינות ביום שלישי ב-14:00 או ביום רביעי ב-10:00 בבוקר. מה הכי נוח לך?
>
> בברכה,
> צוות התפעול

---

### 2. Order Status Inquiry (Customer Service)

**Category:** Customer Service
**Tests:** Order number parsing (#88291), information-request classification, empathetic tone in reply

**Subject:** שאלה לגבי הזמנה מספר #88291

**Body:**
> שלום רב,
>
> ביצעתי הזמנה באתר לפני שבועיים ועדיין לא קיבלתי עדכון על המשלוח.
> אשמח לדעת איפה זה עומד ואם יש מספר מעקב שניתן לבדוק מול חברת השליחויות.
>
> תודה,
> נועה

---

### 3. Urgent System Error

**Category:** Urgent / Technical
**Tests:** Urgency detection, escalation behavior, high-priority classification, fast response generation

**Subject:** דחוף! תקלה בגישה למערכת

**Body:**
> היי,
>
> אני מנסה להתחבר למסד הנתונים ומתקבלת שגיאת 500. זה עוצר את כל צוות הפיתוח כרגע.
> מישהו יכול להעיף מבט בדחיפות?

---

### 4. Weekly Status Update (Report)

**Category:** Informational / Report
**Tests:** Recognizing emails that require no reply, summary generation, passive tone handling

**Subject:** סיכום שבועי – פרויקט ה-Agent

**Body:**
> היי לכולם,
>
> רציתי לעדכן שהשבוע סיימנו את בניית התשתית של הבוט.
> השלב הבא הוא אינטגרציה עם ה-API של המיילים ובדיקות עומסים.
> כרגע אנחנו עומדים בלוחות הזמנים.
>
> שבת שלום!

---

### 5. Soft Marketing / Collaboration Proposal

**Category:** Unsolicited / Marketing
**Tests:** Spam-like classification, non-actionable email handling, polite decline or ignore suggestions

**Subject:** הצעה לשדרוג תהליכי העבודה בצוות שלך

**Body:**
> שלום,
>
> ראיתי שאתם עוסקים בתחום ה-AI והנדסת נתונים.
> פיתחנו כלי חדש שיכול לחסוך לכם 20% מהזמן המוקדש לסינון מיילים.
> נשמח לקבוע שיחה קצרה להדגמה.
>
> בברכה,
> אלכס

---

### 6. Personal / Social Email

**Category:** Personal / Informal
**Tests:** Informal tone detection, social context awareness, casual reply generation

**Subject:** יום הולדת לעידו

**Body:**
> היי נועה,
>
> מה קורה? חשבתי שאולי כדאי שנתחיל לתכנן מה עושים ביומולדת של עידו.
> יש לך רעיון למסעדה טובה או אולי עדיף לעשות משהו בבית עם חברים?
> דברי איתי.

---

## Generic Response Template (Agent Reference)

This is the base reply template the agent should adapt for each email type:

```
Subject: Re: [original subject]

היי [שם השולח - אם זוהה, אחרת "שלום"],

תודה על המייל. קיבלתי את הפנייה שלך בנושא [תמצית הנושא המקורי במשפט אחד].

[פסקה של התייחסות ספציפית – אישור קבלת פרטים / קביעת מועד / בקשת הבהרה]

אני בודק/ת את העניין ואחזור אליך עם עדכון נוסף ברגע שיהיה לי מידע חדש.
במידה ומדובר במשהו דחוף, ניתן ליצור איתי קשר גם ב-[אמצעי קשר חלופי].

בברכה,
[שם / שם האייגנט]
```

---

## Edge Case Suggestions

To stress-test the agent beyond the standard examples above, consider also sending:

- An email with **no subject line**
- An email containing **only a URL**
- An email with **intentional spelling mistakes**
- A **very long email** (500+ words) to test truncation handling
- An email written in a **mix of Hebrew and English**
