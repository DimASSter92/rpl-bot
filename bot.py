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
from understatapi import UnderstatClient
from aiohttp import web

# === НАСТРОЙКИ ===
API_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
if not API_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не найден в переменных окружения!")

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

# === КНОПКИ ===
def get_main_menu():
    keyboard = InlineKeyboardMarkup(row_width=2)
    buttons = [
        InlineKeyboardButton("📅 Расписание", callback_data="menu_matches"),
        InlineKeyboardButton("🏆 Таблица", callback_data="menu_rating"),
        InlineKeyboardButton("📋 Мои прогнозы", callback_data="menu_mypredictions"),
        InlineKeyboardButton("🏅 Чемпион", callback_data="menu_champion"),
        InlineKeyboardButton("⬇️ Вылет", callback_data="menu_relegation"),
        InlineKeyboardButton("❓ Помощь", callback_data="menu_help"),
    ]
    keyboard.add(*buttons)
    return keyboard

def get_match_predict_keyboard(match_id: int):
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
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="menu_back"))
    return keyboard

def get_back_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="menu_back"))
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

# === АВТОМАТИЧЕСКОЕ ПОЛУЧЕНИЕ МАТЧЕЙ ===
async def check_and_update_matches():
    try:
        with UnderstatClient() as understat:
            league_data = understat.league(league="RFPL").get_match_data(season="2026")
            
            if not league_data:
                logging.info("Нет данных о матчах РПЛ")
                return
            
            for match_data in league_data:
                existing_match = session.query(Match).filter_by(
                    team1=match_data.get('h', {}).get('title', ''),
                    team2=match_data.get('a', {}).get('title', '')
                ).first()
                
                is_finished = match_data.get('isResult', False) or match_data.get('status') == 'Finished'
                
                if existing_match:
                    if is_finished and not existing_match.finished:
                        home_score = match_data.get('goals', {}).get('h', 0)
                        away_score = match_data.get('goals', {}).get('a', 0)
                        existing_match.score1 = home_score
                        existing_match.score2 = away_score
                        existing_match.finished = True
                        session.commit()
                        await process_match_results(existing_match)
                        logging.info(f"✅ Матч завершён: {existing_match.team1} {home_score}:{away_score} {existing_match.team2}")
                else:
                    match = Match(
                        team1=match_data.get('h', {}).get('title', 'Unknown'),
                        team2=match_data.get('a', {}).get('title', 'Unknown'),
                        match_date=datetime.now(),
                        finished=is_finished
                    )
                    if is_finished:
                        match.score1 = match_data.get('goals', {}).get('h', 0)
                        match.score2 = match_data.get('goals', {}).get('a', 0)
                    session.add(match)
                    session.commit()
                    logging.info(f"✅ Добавлен матч: {match.team1} ⚔️ {match.team2}")
                    
    except Exception as e:
        logging.error(f"Ошибка при получении данных: {e}")

async def process_match_results(match: Match):
    predictions = session.query(Prediction).filter_by(match_id=match.id).all()
    for p in predictions:
        points = calculate_match_points(p, match)
        p.points_earned = points
        user = session.query(User).filter_by(id=p.user_id).first()
        if user:
            user.points += points
    session.commit()
    logging.info(f"🎯 Начислены очки за матч {match.team1} - {match.team2}")

async def periodic_check():
    while True:
        await check_and_update_matches()
        await asyncio.sleep(300)

# === КОМАНДА /START ===
@dp.message(Command("start"))
async def start_command(message: Message):
    get_or_create_user(message)
    await message.answer(
        "⚽ *Добро пожаловать в конкурс прогнозов РПЛ 2026/27!*\n\n"
        "Выберите действие:",
        reply_markup=get_main_menu(),
        parse_mode="Markdown"
    )

@dp.message(Command("help"))
async def help_command(message: Message):
    await message.answer(
        "⚽ *Правила:*\n"
        "• Точный счет — 3 очка\n"
        "• Угаданный исход — 1 очко\n"
        "• Чемпион — +10 очков\n"
        "• Каждая угаданная команда на вылет — +5 очков\n\n"
        "🤖 Матчи и результаты подгружаются автоматически!",
        reply_markup=get_back_keyboard(),
        parse_mode="Markdown"
    )

@dp.message(Command("addmatch"))
async def add_match(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Только для администратора.")
        return
    parts = message.text.split(maxsplit=3)
    if len(parts) != 4:
        await message.answer("❌ Формат: `/addmatch Команда1 Команда2 ДД.ММ.ГГГГ ЧЧ:ММ`")
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
            await message.answer("❌ Матч не найден.")
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
        await message.answer(f"✅ Результат: {match.team1} {s1}:{s2} {match.team2}\n🎯 Очки начислены!")
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
    for user in users:
        if user.champion == champion:
            user.points += 10
    session.commit()
    await message.answer(f"✅ Чемпион: *{champion}* (+10 очков угадавшим)", parse_mode="Markdown")

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
    for user in users:
        if user.relegated_teams:
            user_teams = json.loads(user.relegated_teams)
            for team in user_teams:
                if team in relegated:
                    user.points += 5
    session.commit()
    await message.answer(f"✅ Вылетевшие: *{relegated[0]}, {relegated[1]}* (+5 за каждую)", parse_mode="Markdown")

# === ОБРАБОТЧИК КНОПОК ===
@dp.callback_query()
async def handle_callback(callback: CallbackQuery):
    data = callback.data
    user = get_user_by_callback(callback)
    
    if data == "menu_back":
        await callback.message.edit_text(
            "⚽ *Главное меню*\n\nВыберите действие:",
            reply_markup=get_main_menu(),
            parse_mode="Markdown"
        )
        await callback.answer()
        return
    
    elif data == "menu_matches":
        matches = session.query(Match).filter_by(finished=False).order_by(Match.match_date).all()
        if not matches:
            await callback.message.edit_text(
                "📭 Нет активных матчей.\n\nПодождите, данные загружаются автоматически.",
                reply_markup=get_back_keyboard()
            )
            await callback.answer()
            return
        
        text = "📅 *Матчи:*\n\n"
        for i, m in enumerate(matches, 1):
            date_str = m.match_date.strftime("%d.%m %H:%M") if m.match_date else "Дата не указана"
            text += f"{i}. {m.team1} ⚔️ {m.team2} ({date_str}) | ID: `{m.id}`\n"
        
        keyboard = InlineKeyboardMarkup(row_width=1)
        for m in matches:
            keyboard.add(
                InlineKeyboardButton(
                    f"📝 Прогноз: {m.team1} ⚔️ {m.team2}",
                    callback_data=f"select_predict_{m.id}"
                )
            )
        keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="menu_back"))
        
        await callback.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        await callback.answer()
        return
    
    elif data == "menu_rating":
        users = session.query(User).filter_by(league_id=user.league_id).order_by(User.points.desc()).all()
        if not users:
            await callback.message.edit_text(
                "Пока никого нет в рейтинге.",
                reply_markup=get_back_keyboard()
            )
            await callback.answer()
            return
        
        text = "🏆 *Таблица лидеров:*\n\n"
        for i, u in enumerate(users, 1):
            medal = "🥇" if i == 1 else ("🥈" if i == 2 else ("🥉" if i == 3 else f"{i}."))
            name = u.full_name or u.username or str(u.telegram_id)
            text += f"{medal} {name} — {u.points} очков\n"
        
        await callback.message.edit_text(
            text,
            reply_markup=get_back_keyboard(),
            parse_mode="Markdown"
        )
        await callback.answer()
        return
    
    elif data == "menu_mypredictions":
        preds = session.query(Prediction).join(Match).filter(Prediction.user_id == user.id).all()
        if not preds:
            await callback.message.edit_text(
                "📭 У вас нет прогнозов.",
                reply_markup=get_back_keyboard()
            )
            await callback.answer()
            return
        
        text = "📋 *Ваши прогнозы:*\n\n"
        for p in preds:
            status = "✅ Завершён" if p.match.finished else "⏳ Ожидает"
            result_str = f"{p.match.score1}:{p.match.score2}" if p.match.finished else "?"
            text += f"{p.match.team1} {p.pred_score1}:{p.pred_score2} {p.match.team2} | Результат: {result_str} | Очки: {p.points_earned} | {status}\n"
        
        await callback.message.edit_text(
            text,
            reply_markup=get_back_keyboard(),
            parse_mode="Markdown"
        )
        await callback.answer()
        return
    
    elif data == "menu_champion":
        text = "🏅 *Прогноз на чемпиона*\n\n"
        if user.champion:
            text += f"Ваш прогноз: *{user.champion}*\n"
        else:
            text += "Вы ещё не сделали прогноз на чемпиона.\n\n"
        text += "Используйте команду:\n`/champion Название_команды`\n\nПример: `/champion Зенит`"
        
        await callback.message.edit_text(
            text,
            reply_markup=get_back_keyboard(),
            parse_mode="Markdown"
        )
        await callback.answer()
        return
    
    elif data == "menu_relegation":
        text = "⬇️ *Прогноз на вылет*\n\n"
        if user.relegated_teams:
            teams = json.loads(user.relegated_teams)
            text += f"Ваш прогноз: *{teams[0]}, {teams[1]}*\n"
        else:
            text += "Вы ещё не сделали прогноз на вылет.\n\n"
        text += "Используйте команду:\n`/relegation Команда1, Команда2`\n\nПример: `/relegation Химки, Факел`"
        
        await callback.message.edit_text(
            text,
            reply_markup=get_back_keyboard(),
            parse_mode="Markdown"
        )
        await callback.answer()
        return
    
    elif data == "menu_help":
        await callback.message.edit_text(
            "⚽ *Правила игры:*\n\n"
            "📌 *Прогнозы на матчи:*\n"
            "• Точный счет — 3 очка\n"
            "• Угаданный исход — 1 очко\n\n"
            "🏆 *Сезонные прогнозы:*\n"
            "• Чемпион — +10 очков\n"
            "• Каждая угаданная команда на вылет — +5 очков\n\n"
            "🤖 Матчи и результаты подгружаются автоматически!\n"
            "📝 Чтобы сделать прогноз, выберите матч в разделе 'Расписание'",
            reply_markup=get_back_keyboard(),
            parse_mode="Markdown"
        )
        await callback.answer()
        return
    
    elif data.startswith("select_predict_"):
        match_id = int(data.split("_")[2])
        match = session.query(Match).filter_by(id=match_id, finished=False).first()
        
        if not match:
            await callback.message.edit_text(
                "❌ Этот матч уже завершён или не найден.",
                reply_markup=get_back_keyboard()
            )
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
            reply_markup=get_match_predict_keyboard(match.id),
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
            await callback.message.edit_text(
                "❌ Этот матч уже завершён.",
                reply_markup=get_back_keyboard()
            )
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
            f"✅ Угаданный исход: +1 очко",
            reply_markup=get_back_keyboard(),
            parse_mode="Markdown"
        )
        await callback.answer()
        return

# === ЗАПУСК БОТА И ВЕБ-СЕРВЕРА ===
async def start_bot():
    await dp.start_polling(bot)

async def health_check(request):
    return web.Response(text="✅ Бот работает!")

async def main():
    asyncio.create_task(periodic_check())
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
    
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
