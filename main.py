import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

with open("env.txt", "r") as f:
    for line in f:
        if line.startswith("BOT_TOKEN="):
            TOKEN = line.strip().split("=")[1]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет, Толик! Я твой ChatGPT Mentor бот 💬")

async def goals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎯 Цели на месяц:\n1. Завершить сайт hurtova.com.ua\n2. Запустить Telegram-бота\n3. Настроить Shopify")

async def plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📌 План на сегодня:\n– Добавить 10 товаров\n– Проверить адаптив Tilda\n– Повторить английский")

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("goals", goals))
    app.add_handler(CommandHandler("plan", plan))
    print("🤖 Бот запущен!")
    app.run_polling()
