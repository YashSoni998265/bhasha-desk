"""Canned translations for `bhasha demo run`, the replay that needs no Switch and no AI.

Written the way the translator agent's first drafts come out: "AutoPay" is
rendered in each script until a reviewer decides it stays in English.
"""

TAMIL_DRAFT = {
    "autopay.title": "UPI ஆட்டோபே",
    "autopay.subtitle": "பில்களை ஒவ்வொரு மாதமும் தானாகச் செலுத்துங்கள்.",
    "autopay.setup_cta": "ஆட்டோபே அமைக்கவும்",
    "autopay.mandate_info": "{merchant}-க்கு மாதம் ₹{limit} வரையிலான ஆணையை அனுமதிக்கிறீர்கள்.",
    "autopay.next_debit": "அடுத்த பிடித்தம்: {date}",
    "autopay.pause_cta": "இடைநிறுத்து",
    "autopay.cancel_warning": "ரத்து செய்தால் <b>{merchant}</b>-க்கு இனி பணம் செலுத்தப்படாது.",
    "autopay.reminders.one": "ஒவ்வொரு பிடித்தத்திற்கும் முன் {count} நினைவூட்டல் வரும்.",
    "autopay.reminders.other": "ஒவ்வொரு பிடித்தத்திற்கும் முன் {count} நினைவூட்டல்கள் வரும்.",
    "autopay.limit_error": "₹1 முதல் ₹{max} வரை ஒரு தொகையை உள்ளிடவும்.",
}
TAMIL_KEEP_AUTOPAY = {"autopay.title": "UPI AutoPay", "autopay.setup_cta": "AutoPay அமைக்கவும்"}

HINDI_DRAFT = {
    "autopay.title": "UPI ऑटोपे",
    "autopay.subtitle": "हर महीने अपने बिल समय पर अपने-आप भरें।",
    "autopay.setup_cta": "ऑटोपे सेट करें",
    "autopay.mandate_info": "आप {merchant} के लिए हर महीने ₹{limit} तक के मैंडेट को मंज़ूरी दे रहे हैं।",
    "autopay.next_debit": "अगला डेबिट {date} को",
    "autopay.pause_cta": "रोकें",
    "autopay.cancel_warning": "रद्द करने पर <b>{merchant}</b> को आगे के सभी पेमेंट रुक जाएंगे।",
    "autopay.reminders.one": "हर डेबिट से पहले आपको {count} रिमाइंडर मिलेगा।",
    "autopay.reminders.other": "हर डेबिट से पहले आपको {count} रिमाइंडर मिलेंगे।",
    "autopay.limit_error": "₹1 से ₹{max} के बीच कोई रकम डालें।",
}
HINDI_KEEP_AUTOPAY = {"autopay.title": "UPI AutoPay", "autopay.setup_cta": "AutoPay सेट करें"}
HINDI_LATE_EDIT = {"autopay.pause_cta": "रोक दें"}
