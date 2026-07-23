import asyncio
import logging
import json
import os
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, ForeignKey, Text
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from aiohttp import web

# === НАСТРОЙКИ ===
API_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
if not API_TOKEN:
    API_TOKEN = "ВАШ_ТОКЕН_ОТ_BOTFATHER"

ADMIN_ID = int(os.environ.get('ADMIN_ID', 378215323))

# === БАЗА ДАННЫХ ===
engine = create_engine('sqlite:///predictions.db', echo=False)
Base = declarative_base()
Session = sessionmaker(bind=engine)
session = Session()

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True)
    username = Column(String)
    full_name = Column(String)
    league_id = Column(Integer, ForeignKey('leagues.id'))
    points = Column(Integer, default=0)
    champion = Column(String, nullable=True)
    relegated_teams = Column(Text, nullable=True)
    predictions = relationship("Prediction", back_populates="user")
    league = relationship("League", back_populates="users")

class League(Base):
    __tablename__ = 'leagues'
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True)
    created_by = Column(Integer)
    users = relationship("User", back_populates="league")

class Match(Base):
    __tablename__ = 'matches'
    id = Column(Integer, primary_key=True)
    team1 = Column(String)
    team2 = Column(String)
    match_date = Column(DateTime)
    finished = Column(Boolean, default=False)
    score1 = Column(Integer, nullable=True)
    score2 = Column(Integer, nullable=True)
    predictions = relationship("Prediction", back_populates="match")

class Prediction(Base):
    __tablename__ = 'predictions'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    match_id = Column(Integer, ForeignKey('matches.id'))
    pred_score1 = Column(Integer)
    pred_score2 = Column(Integer)
    points_earned = Column(Integer, default=0)
    user = relationship("User", back_populates="predictions")
    match = relationship("Match", back_populates="predictions")

Base.metadata.create_all(engine)

# === ИНИЦИАЛИЗАЦИЯ ===
bot = Bot(token=API_TOKEN)
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)

# === КЛАВИАТУРА ДЛЯ ВЫБОРА СЧЁТА ===
def get_score_keyboard(match_id: int):
    keyboard = InlineKeyboardMarkup(row_width=4)
    buttons = []
    for s1 in range(0, 5):
        for s2 in range(0, 5):
            buttons.append(
                InlineKeyboardButton(
                    f"{s1}:{s2}",
                    callback_data=f"predict_{match_id}_{s1}_{s2}"
                )
            )
    keyboard.add(*buttons[:12])
    return keyboard

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
def get_or_create_user(message: Message):
    user = session.query(User).filter_by(telegram_id=message.from_user.id).first()
    if not user:
        league = session.query(League).first()
        if not league:
            league = League(name="РПЛ 2026/27", created_by=ADMIN_ID)
            session.add(league)
            session.commit()
        user = User(
            telegram_id=message.from_user.id,
            username=message.from_user.username or "",
            full_name=message.from_user.full_name or "",
            league_id=league.id
        )
        session.add(user)
        session.commit()
    return user

def get_user_by_callback(callback: CallbackQuery):
    user = session.query(User).filter_by(telegram_id=callback.from_user.id).first()
    if not user:
        league = session.query(League).first()
        if not league:
            league = League(name="РПЛ 2026/27", created_by=ADMIN_ID)
            session.add(league)
            session.commit()
        user = User(
            telegram_id=callback.from_user.id,
            username=callback.from_user.username or "",
            full_name=callback.from_user.full_name or "",
            league_id=league.id
        )
        session.add(user)
        session.commit()
    return user

def calculate_match_points(pred: Prediction, match: Match) -> int:
    points = 0
    pred_winner = 1 if pred.pred_score1 > pred.pred_score2 else (2 if pred.pred_score1 < pred.pred_score2 else 0)
    real_winner = 1 if match.score1 > match.score2 else (2 if match.score1 < match.score2 else 0)
    
    if pred.pred_score1 == match.score1 and pred.pred_score2 == match.score2:
        points = 3
    elif pred_winner == real_winner:
        points = 1
    return points

# === КОМАНДЫ БОТА ===

@dp.message(Command("start"))
async def start_command(message: Message):
    get_or_create_user(message)
    await message.answer(
        "⚽ *Добро пожаловать в конкурс прогнозов РПЛ 2026/27!*\n\n"
        "📋 *Доступные команды:*\n"
        "/matches — список матчей (будущие и прошедшие)\n"
        "/rating — таблица лидеров\n"
        "/mypredictions — мои прогнозы\n"
        "/champion [команда] — прогноз на чемпиона (+10 очков)\n"
        "/relegation [команда1, команда2] — прогноз на вылет (+5 за каждую)\n"
        "/help — правила игры\n\n"
        "👑 *Админ-команды:*\n"
        "/addmatch Т1 Т2 ДД.ММ.ГГГГ ЧЧ:ММ — добавить матч\n"
        "/setresult ID S1 S2 — ввести результат\n"
        "/setchampion [команда] — установить чемпиона\n"
        "/setrelegated [команда1, команда2] — установить вылетевших",
        parse_mode="Markdown"
    )

@dp.message(Command("help"))
async def help_command(message: Message):
    await message.answer(
        "⚽ *Правила игры:*\n\n"
        "📌 *Прогнозы на матчи:*\n"
        "• Точный счёт — 3 очка\n"
        "• Угаданный исход (победа/ничья) — 1 очко\n\n"
        "🏆 *Сезонные прогнозы:*\n"
        "• Чемпион — +10 очков\n"
        "• Каждая угаданная команда на вылет — +5 очков\n\n"
        "📝 *Как сделать прогноз:*\n"
        "1. Используйте команду `/predict X Y` (например, `/predict 2 1`)\n"
        "2. Или выберите матч в `/matches` и нажмите на счёт",
        parse_mode="Markdown"
    )

@dp.message(Command("matches"))
async def list_matches(message: Message):
    user = get_or_create_user(message)
    
    # Будущие матчи
    future_matches = session.query(Match).filter_by(finished=False).order_by(Match.match_date).all()
    # Прошедшие матчи (последние 10)
    past_matches = session.query(Match).filter_by(finished=True).order_by(Match.match_date.desc()).limit(10).all()
    
    # === БУДУЩИЕ МАТЧИ ===
    if future_matches:
        text = "🔮 *Будущие матчи:*\n\n"
        for m in future_matches:
            date_str = m.match_date.strftime("%d.%m %H:%M") if m.match_date else "Дата не указана"
            pred = session.query(Prediction).filter_by(user_id=user.id, match_id=m.id).first()
            status = f"✅ {pred.pred_score1}:{pred.pred_score2}" if pred else "❌ Нет прогноза"
            text += f"• {m.team1} ⚔️ {m.team2} ({date_str}) — {status}\n"
        
        # Кнопки для выбора матча для прогноза
        keyboard = InlineKeyboardMarkup(row_width=1)
        for m in future_matches[:5]:
            keyboard.add(
                InlineKeyboardButton(
                    f"📝 {m.team1} ⚔️ {m.team2}",
                    callback_data=f"select_predict_{m.id}"
                )
            )
        await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await message.answer("📭 Нет будущих матчей. Добавьте через /addmatch")
    
    # === ПРОШЕДШИЕ МАТЧИ ===
    if past_matches:
        text = "\n\n✅ *Прошедшие матчи:*\n\n"
        for m in past_matches:
            date_str = m.match_date.strftime("%d.%m %H:%M") if m.match_date else "Дата не указана"
            pred = session.query(Prediction).filter_by(user_id=user.id, match_id=m.id).first()
            if pred:
                text += f"• {m.team1} {m.score1}:{m.score2} {m.team2} ({date_str}) — Очки: {pred.points_earned}\n"
            else:
                text += f"• {m.team1} {m.score1}:{m.score2} {m.team2} ({date_str}) — ❌ Нет прогноза\n"
        await message.answer(text, parse_mode="Markdown")

@dp.message(Command("predict"))
async def make_prediction(message: Message):
    user = get_or_create_user(message)
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("❌ Формат: `/predict голы1 голы2`\nПример: `/predict 2 1`", parse_mode="Markdown")
        return
    try:
        s1, s2 = int(parts[1]), int(parts[2])
    except ValueError:
        await message.answer("❌ Введите числа.")
        return
    
    # Ближайший будущий матч
    match = session.query(Match).filter_by(finished=False).order_by(Match.match_date).first()
    if not match:
        await message.answer("❌ Нет будущих матчей. Добавьте через /addmatch")
        return
    
    existing = session.query(Prediction).filter_by(user_id=user.id, match_id=match.id).first()
    if existing:
        existing.pred_score1 = s1
        existing.pred_score2 = s2
    else:
        pred = Prediction(user_id=user.id, match_id=match.id, pred_score1=s1, pred_score2=s2)
        session.add(pred)
    session.commit()
    await message.answer(f"✅ Прогноз сохранён: {match.team1} {s1}:{s2} {match.team2}")

@dp.message(Command("mypredictions"))
async def my_predictions(message: Message):
    user = get_or_create_user(message)
    preds = session.query(Prediction).join(Match).filter(Prediction.user_id == user.id).all()
    if not preds:
        await message.answer("📭 У вас нет прогнозов.")
        return
    text = "📋 *Ваши прогнозы:*\n\n"
    for p in preds:
        status = "✅ Завершён" if p.match.finished else "⏳ Ожидает"
        result_str = f"{p.match.score1}:{p.match.score2}" if p.match.finished else "?"
        text += f"• {p.match.team1} {p.pred_score1}:{p.pred_score2} {p.match.team2} | Результат: {result_str} | Очки: {p.points_earned} | {status}\n"
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("rating"))
async def show_rating(message: Message):
    user = get_or_create_user(message)
    users = session.query(User).filter_by(league_id=user.league_id).order_by(User.points.desc()).all()
    if not users:
        await message.answer("Пока никого нет в рейтинге.")
        return
    text = "🏆 *Таблица лидеров:*\n\n"
    for i, u in enumerate(users, 1):
        medal = "🥇" if i == 1 else ("🥈" if i == 2 else ("🥉" if i == 3 else f"{i}."))
        name = u.full_name or u.username or str(u.telegram_id)
        text += f"{medal} {name} — {u.points} очков\n"
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("champion"))
async def set_champion_predict(message: Message):
    user = get_or_create_user(message)
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("❌ Формат: `/champion Команда`\nПример: `/champion Зенит`", parse_mode="Markdown")
        return
    user.champion = parts[1].strip()
    session.commit()
    await message.answer(f"✅ Прогноз на чемпиона: *{user.champion}* (+10 очков)", parse_mode="Markdown")

@dp.message(Command("relegation"))
async def set_relegation_predict(message: Message):
    user = get_or_create_user(message)
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("❌ Формат: `/relegation Команда1, Команда2`\nПример: `/relegation Химки, Факел`", parse_mode="Markdown")
        return
    teams = [t.strip() for t in parts[1].split(',')]
    if len(teams) != 2:
        await message.answer("❌ Укажите ровно 2 команды через запятую.")
        return
    user.relegated_teams = json.dumps(teams)
    session.commit()
    await message.answer(f"✅ Прогноз на вылет: *{teams[0]}, {teams[1]}* (+5 за каждую)", parse_mode="Markdown")

@dp.message(Command("myseason"))
async def my_season_predictions(message: Message):
    user = get_or_create_user(message)
    text = "🏆 *Мои сезонные прогнозы:*\n\n"
    if user.champion:
        text += f"🏅 Чемпион: *{user.champion}*\n"
    else:
        text += "🏅 Чемпион: ❌ не указан\n"
    if user.relegated_teams:
        teams = json.loads(user.relegated_teams)
        text += f"⬇️ Вылет: *{teams[0]}, {teams[1]}*"
    else:
        text += "⬇️ Вылет: ❌ не указан"
    await message.answer(text, parse_mode="Markdown")

# === АДМИН-КОМАНДЫ ===

@dp.message(Command("addmatch"))
async def add_match(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Только для администратора.")
        return
    parts = message.text.split(maxsplit=3)
    if len(parts) != 4:
        await message.answer(
            "❌ Формат: `/addmatch Команда1 Команда2 ДД.ММ.ГГГГ ЧЧ:ММ`\n"
            "Пример: `/addmatch Зенит Спартак 24.07.2026 20:00`"
        )
        return
    try:
        team1, team2 = parts[1], parts[2]
        dt = datetime.strptime(parts[3], "%d.%m.%Y %H:%M")
        match = Match(team1=team1, team2=team2, match_date=dt)
        session.add(match)
        session.commit()
        await message.answer(f"✅ Матч добавлен: {team1} ⚔️ {team2} ({parts[3]})")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

@dp.message(Command("setresult"))
async def set_result(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Только для администратора.")
        return
    parts = message.text.split()
    if len(parts) != 4:
        await message.answer("❌ Формат: `/setresult ID_матча голы1 голы2`")
        return
    try:
        match_id = int(parts[1])
        s1, s2 = int(parts[2]), int(parts[3])
        match = session.query(Match).filter_by(id=match_id).first()
        if not match:
            await message.answer("❌ Матч не найден. Используйте ID из `/matches`")
            return
        match.score1 = s1
        match.score2 = s2
        match.finished = True
        
        predictions = session.query(Prediction).filter_by(match_id=match.id).all()
        for p in predictions:
            points = calculate_match_points(p, match)
            p.points_earned = points
            user = session.query(User).filter_by(id=p.user_id).first()
            if user:
                user.points += points
        session.commit()
        await message.answer(
            f"✅ Результат: {match.team1} {s1}:{s2} {match.team2}\n"
            f"🎯 Начислено очков: {len(predictions)} игрокам!"
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

@dp.message(Command("setchampion"))
async def set_champion_result(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Только для администратора.")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("❌ Формат: `/setchampion Команда`")
        return
    champion = parts[1].strip()
    users = session.query(User).all()
    count = 0
    for user in users:
        if user.champion == champion:
            user.points += 10
            count += 1
    session.commit()
    await message.answer(f"✅ Чемпион: *{champion}*\n🎯 +10 очков получили {count} игроков!", parse_mode="Markdown")

@dp.message(Command("setrelegated"))
async def set_relegated_result(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Только для администратора.")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("❌ Формат: `/setrelegated Команда1, Команда2`")
        return
    relegated = [t.strip() for t in parts[1].split(',')]
    if len(relegated) != 2:
        await message.answer("❌ Укажите ровно 2 команды.")
        return
    users = session.query(User).all()
    count = 0
    for user in users:
        if user.relegated_teams:
            user_teams = json.loads(user.relegated_teams)
            for team in user_teams:
                if team in relegated:
                    user.points += 5
                    count += 1
    session.commit()
    await message.answer(
        f"✅ Вылетевшие: *{relegated[0]}, {relegated[1]}*\n"
        f"🎯 +5 очков за каждую угаданную команду получили {count} игроков!",
        parse_mode="Markdown"
    )

# === ОБРАБОТЧИК КНОПОК ===

@dp.callback_query()
async def handle_callback(callback: CallbackQuery):
    data = callback.data
    user = get_user_by_callback(callback)
    
    if data.startswith("select_predict_"):
        match_id = int(data.split("_")[2])
        match = session.query(Match).filter_by(id=match_id, finished=False).first()
        
        if not match:
            await callback.message.edit_text("❌ Этот матч уже завершён или не найден.")
            await callback.answer()
            return
        
        existing = session.query(Prediction).filter_by(
            user_id=user.id,
            match_id=match.id
        ).first()
        
        text = f"📝 *Прогноз на матч:*\n\n"
        text += f"{match.team1} ⚔️ {match.team2}\n\n"
        if existing:
            text += f"Ваш текущий прогноз: *{existing.pred_score1}:{existing.pred_score2}*\n\n"
        text += "Выберите счёт:"
        
        await callback.message.edit_text(
            text,
            reply_markup=get_score_keyboard(match.id),
            parse_mode="Markdown"
        )
        await callback.answer()
        return
    
    elif data.startswith("predict_"):
        parts = data.split("_")
        match_id = int(parts[1])
        s1, s2 = int(parts[2]), int(parts[3])
        
        match = session.query(Match).filter_by(id=match_id).first()
        if not match or match.finished:
            await callback.message.edit_text("❌ Этот матч уже завершён.")
            await callback.answer()
            return
        
        existing = session.query(Prediction).filter_by(
            user_id=user.id,
            match_id=match.id
        ).first()
        
        if existing:
            existing.pred_score1 = s1
            existing.pred_score2 = s2
        else:
            pred = Prediction(
                user_id=user.id,
                match_id=match.id,
                pred_score1=s1,
                pred_score2=s2
            )
            session.add(pred)
        session.commit()
        
        await callback.message.edit_text(
            f"✅ *Прогноз сохранён!*\n\n"
            f"{match.team1} *{s1}:{s2}* {match.team2}\n\n"
            f"🏆 Точный счёт: +3 очка\n"
            f"✅ Угаданный исход: +1 очко"
        )
        await callback.answer()
        return

# === ЗАПУСК ===
async def start_bot():
    await dp.start_polling(bot)

async def health_check(request):
    return web.Response(text="✅ Бот работает!")

async def main():
    asyncio.create_task(start_bot())
    
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    
    port = int(os.environ.get('PORT', 10000))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logging.info(f"✅ Веб-сервер запущен на порту {port}")
    
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
    
