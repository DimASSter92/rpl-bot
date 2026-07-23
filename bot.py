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

ADMIN_ID = int(os.environ.get('ADMIN_ID', 378215323))  # ВАШ TELEGRAM ID

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
    correct_scores = Column(Integer, default=0)
    correct_outcomes = Column(Integer, default=0)
    wrong_predictions = Column(Integer, default=0)
    champion = Column(String, nullable=True)
    champion_locked = Column(Boolean, default=False)
    relegated_teams = Column(Text, nullable=True)
    relegated_locked = Column(Boolean, default=False)
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
    is_correct_score = Column(Boolean, default=False)
    is_correct_outcome = Column(Boolean, default=False)
    user = relationship("User", back_populates="predictions")
    match = relationship("Match", back_populates="predictions")

Base.metadata.create_all(engine)

# === ИНИЦИАЛИЗАЦИЯ ===
bot = Bot(token=API_TOKEN)
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)

# === КНОПКИ ДЛЯ ВЫБОРА СЧЁТА ===
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

def calculate_match_stats(pred: Prediction, match: Match):
    points = 0
    is_correct_score = False
    is_correct_outcome = False
    
    pred_winner = 1 if pred.pred_score1 > pred.pred_score2 else (2 if pred.pred_score1 < pred.pred_score2 else 0)
    real_winner = 1 if match.score1 > match.score2 else (2 if match.score1 < match.score2 else 0)
    
    if pred.pred_score1 == match.score1 and pred.pred_score2 == match.score2:
        points = 3
        is_correct_score = True
        is_correct_outcome = True
    elif pred_winner == real_winner:
        points = 1
        is_correct_outcome = True
    
    return points, is_correct_score, is_correct_outcome

# === КОМАНДЫ БОТА ===

@dp.message(Command("start"))
async def start_command(message: Message):
    get_or_create_user(message)
    await message.answer(
        "⚽ *Добро пожаловать в конкурс прогнозов РПЛ 2026/27!*\n\n"
        "📋 *Доступные команды:*\n"
        "/rating — таблица лидеров\n"
        "/predict — прогнозы (мои и доступные матчи)\n"
        "/results — прошедшие матчи с результатами\n"
        "/rules — правила игры\n"
        "/champion [команда] — прогноз на чемпиона (+10 очков)\n"
        "/relegation [команда1, команда2] — прогноз на вылет (+5 за каждую)\n\n"
        "👑 *Админ-команды:*\n"
        "/addmatch Т1 Т2 ДД.ММ.ГГГГ ЧЧ:ММ — добавить матч\n"
        "/setresult ID S1 S2 — ввести результат\n"
        "/setchampion [команда] — установить чемпиона\n"
        "/setrelegated [команда1, команда2] — установить вылетевших\n"
        "/unlock [тип] — разблокировать прогнозы (champion/relegation)",
        parse_mode="Markdown"
    )

@dp.message(Command("rules"))
async def rules_command(message: Message):
    await message.answer(
        "⚽ *Правила игры:*\n\n"
        "📌 *Прогнозы на матчи:*\n"
        "• Точный счёт — 3 очка\n"
        "• Угаданный исход (победа/ничья) — 1 очко\n\n"
        "🏆 *Сезонные прогнозы:*\n"
        "• Чемпион — +10 очков\n"
        "• Каждая угаданная команда на вылет — +5 очков\n\n"
        "📊 *Статистика в таблице лидеров:*\n"
        "• Точные счёты — количество угаданных точных счетов\n"
        "• Угаданные исходы — количество угаданных исходов\n"
        "• Не угадано — количество не угаданных прогнозов\n\n"
        "🔒 *Сезонные прогнозы нельзя изменить после сохранения!*\n\n"
        "🔄 *Прогнозы на матчи можно изменить до начала матча*",
        parse_mode="Markdown"
    )

# === КОМАНДА /RATING (ТАБЛИЦА ЛИДЕРОВ) ===

@dp.message(Command("rating"))
async def show_rating(message: Message):
    user = get_or_create_user(message)
    users = session.query(User).filter_by(league_id=user.league_id).order_by(User.points.desc()).all()
    
    if not users:
        await message.answer("📭 Пока никого нет в рейтинге.")
        return
    
    text = "🏆 *Таблица лидеров:*\n\n"
    for i, u in enumerate(users, 1):
        medal = "🥇" if i == 1 else ("🥈" if i == 2 else ("🥉" if i == 3 else f"{i}."))
        name = u.full_name or u.username or str(u.telegram_id)
        text += (
            f"{medal} *{name}*\n"
            f"   • Очки: {u.points}\n"
            f"   • Точные счета: {u.correct_scores}\n"
            f"   • Угадано исходов: {u.correct_outcomes}\n"
            f"   • Не угадано: {u.wrong_predictions}\n\n"
        )
    
    await message.answer(text, parse_mode="Markdown")

# === КОМАНДА /PREDICT (ПРОГНОЗЫ) ===

@dp.message(Command("predict"))
async def predict_command(message: Message):
    user = get_or_create_user(message)
    now = datetime.now()
    
    # === МОИ ПРОГНОЗЫ ===
    my_predictions = session.query(Prediction).join(Match).filter(
        Prediction.user_id == user.id,
        Match.finished == False
    ).all()
    
    text = "📋 *Мои прогнозы:*\n\n"
    keyboard = InlineKeyboardMarkup(row_width=1)
    
    if my_predictions:
        for p in my_predictions:
            match = p.match
            date_str = match.match_date.strftime("%d.%m %H:%M") if match.match_date else "Дата не указана"
            
            # Проверяем, можно ли изменить прогноз (до начала матча)
            can_edit = match.match_date > now if match.match_date else False
            edit_icon = "✏️" if can_edit else "🔒"
            
            text += f"• {match.team1} {p.pred_score1}:{p.pred_score2} {match.team2} ({date_str}) {edit_icon}\n"
            
            if can_edit:
                keyboard.add(
                    InlineKeyboardButton(
                        f"✏️ Изменить: {match.team1} ⚔️ {match.team2}",
                        callback_data=f"edit_predict_{match.id}"
                    )
                )
    else:
        text += "📭 У вас нет активных прогнозов.\n"
    
    # === ДОСТУПНЫЕ МАТЧИ ДЛЯ ПРОГНОЗА ===
    available_matches = session.query(Match).filter(
        Match.finished == False,
        Match.match_date > now
    ).all()
    
    # Убираем матчи, на которые уже есть прогноз
    predicted_ids = [p.match_id for p in my_predictions]
    available_matches = [m for m in available_matches if m.id not in predicted_ids]
    
    if available_matches:
        text += "\n🔮 *Доступные матчи для прогноза:*\n\n"
        for m in available_matches[:10]:  # Ограничим 10 матчами
            date_str = m.match_date.strftime("%d.%m %H:%M") if m.match_date else "Дата не указана"
            text += f"• {m.team1} ⚔️ {m.team2} ({date_str})\n"
            keyboard.add(
                InlineKeyboardButton(
                    f"📝 Прогноз: {m.team1} ⚔️ {m.team2}",
                    callback_data=f"select_predict_{m.id}"
                )
            )
    else:
        text += "\n✅ Все доступные матчи уже имеют прогнозы!"
    
    await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")

# === КОМАНДА /RESULTS (ПРОШЕДШИЕ МАТЧИ) ===

@dp.message(Command("results"))
async def results_command(message: Message):
    user = get_or_create_user(message)
    
    past_matches = session.query(Match).filter_by(finished=True).order_by(Match.match_date.desc()).limit(20).all()
    
    if not past_matches:
        await message.answer("📭 Нет завершённых матчей.")
        return
    
    text = "✅ *Прошедшие матчи:*\n\n"
    for m in past_matches:
        date_str = m.match_date.strftime("%d.%m %H:%M") if m.match_date else "Дата не указана"
        
        pred = session.query(Prediction).filter_by(user_id=user.id, match_id=m.id).first()
        if pred:
            pred_str = f"Ваш прогноз: {pred.pred_score1}:{pred.pred_score2} — Очки: {pred.points_earned}"
        else:
            pred_str = "❌ Нет прогноза"
        
        text += f"• {m.team1} {m.score1}:{m.score2} {m.team2} ({date_str})\n   {pred_str}\n\n"
    
    await message.answer(text, parse_mode="Markdown")

# === КОМАНДА /CHAMPION (ПРОГНОЗ НА ЧЕМПИОНА) ===

@dp.message(Command("champion"))
async def set_champion(message: Message):
    user = get_or_create_user(message)
    
    if user.champion_locked:
        await message.answer(
            f"🔒 *Ваш прогноз на чемпиона заблокирован!*\n"
            f"Вы выбрали: *{user.champion}*\n\n"
            "Изменить прогноз может только администратор.",
            parse_mode="Markdown"
        )
        return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        await message.answer(
            "❌ Формат: `/champion Команда`\nПример: `/champion Зенит`",
            parse_mode="Markdown"
        )
        return
    
    team = parts[1].strip()
    user.champion = team
    user.champion_locked = True
    session.commit()
    
    await message.answer(
        f"✅ Прогноз на чемпиона сохранён: *{team}* (+10 очков)\n\n"
        "🔒 Теперь вы не можете изменить этот прогноз.",
        parse_mode="Markdown"
    )

# === КОМАНДА /RELEGATION (ПРОГНОЗ НА ВЫЛЕТ) ===

@dp.message(Command("relegation"))
async def set_relegation(message: Message):
    user = get_or_create_user(message)
    
    if user.relegated_locked:
        teams = json.loads(user.relegated_teams) if user.relegated_teams else []
        await message.answer(
            f"🔒 *Ваш прогноз на вылет заблокирован!*\n"
            f"Вы выбрали: *{teams[0]}, {teams[1]}*\n\n"
            "Изменить прогноз может только администратор.",
            parse_mode="Markdown"
        )
        return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        await message.answer(
            "❌ Формат: `/relegation Команда1, Команда2`\nПример: `/relegation Химки, Факел`",
            parse_mode="Markdown"
        )
        return
    
    teams = [t.strip() for t in parts[1].split(',')]
    if len(teams) != 2:
        await message.answer("❌ Укажите ровно 2 команды через запятую.")
        return
    
    user.relegated_teams = json.dumps(teams)
    user.relegated_locked = True
    session.commit()
    
    await message.answer(
        f"✅ Прогноз на вылет сохранён: *{teams[0]}, {teams[1]}* (+5 за каждую)\n\n"
        "🔒 Теперь вы не можете изменить этот прогноз.",
        parse_mode="Markdown"
    )

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
            await message.answer("❌ Матч не найден.")
            return
        
        match.score1 = s1
        match.score2 = s2
        match.finished = True
        
        predictions = session.query(Prediction).filter_by(match_id=match.id).all()
        for p in predictions:
            points, correct_score, correct_outcome = calculate_match_stats(p, match)
            p.points_earned = points
            p.is_correct_score = correct_score
            p.is_correct_outcome = correct_outcome
            
            user = session.query(User).filter_by(id=p.user_id).first()
            if user:
                user.points += points
                if correct_score:
                    user.correct_scores += 1
                elif correct_outcome:
                    user.correct_outcomes += 1
                else:
                    user.wrong_predictions += 1
        
        session.commit()
        
        await message.answer(
            f"✅ Результат: {match.team1} {s1}:{s2} {match.team2}\n"
            f"🎯 Обработано прогнозов: {len(predictions)}"
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

@dp.message(Command("setchampion"))
async def set_champion_admin(message: Message):
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
    await message.answer(
        f"✅ Чемпион: *{champion}*\n"
        f"🎯 +10 очков получили {count} игроков!",
        parse_mode="Markdown"
    )

@dp.message(Command("setrelegated"))
async def set_relegated_admin(message: Message):
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
        f"🎯 +5 очков за каждую команду получили {count} игроков!",
        parse_mode="Markdown"
    )

@dp.message(Command("unlock"))
async def unlock_predictions(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Только для администратора.")
        return
    
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("❌ Формат: `/unlock champion` или `/unlock relegation`")
        return
    
    type_ = parts[1].lower()
    users = session.query(User).all()
    
    if type_ == "champion":
        for user in users:
            user.champion_locked = False
        session.commit()
        await message.answer("✅ Прогнозы на чемпиона разблокированы для всех игроков!")
    elif type_ == "relegation":
        for user in users:
            user.relegated_locked = False
        session.commit()
        await message.answer("✅ Прогнозы на вылет разблокированы для всех игроков!")
    else:
        await message.answer("❌ Используйте `/unlock champion` или `/unlock relegation`")

# === ОБРАБОТЧИК КНОПОК ===

@dp.callback_query()
async def handle_callback(callback: CallbackQuery):
    data = callback.data
    user = get_user_by_callback(callback)
    now = datetime.now()
    
    if data.startswith("select_predict_"):
        match_id = int(data.split("_")[2])
        match = session.query(Match).filter_by(id=match_id, finished=False).first()
        
        if not match:
            await callback.message.edit_text("❌ Этот матч уже завершён или не найден.")
            await callback.answer()
            return
        
        # Проверяем, можно ли сделать прогноз (матч ещё не начался)
        if match.match_date and match.match_date <= now:
            await callback.message.edit_text("⏰ Этот матч уже начался, прогнозы недоступны.")
            await callback.answer()
            return
        
        existing = session.query(Prediction).filter_by(
            user_id=user.id,
            match_id=match.id
        ).first()
        
        text = f"📝 *Прогноз на матч:*\n\n"
        text += f"{match.team1} ⚔️ {match.team2}\n"
        text += f"🕐 {match.match_date.strftime('%d.%m.%Y %H:%M') if match.match_date else 'Дата не указана'}\n\n"
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
    
    elif data.startswith("edit_predict_"):
        match_id = int(data.split("_")[2])
        match = session.query(Match).filter_by(id=match_id, finished=False).first()
        
        if not match:
            await callback.message.edit_text("❌ Этот матч уже завершён или не найден.")
            await callback.answer()
            return
        
        # Проверяем, можно ли изменить прогноз (матч ещё не начался)
        if match.match_date and match.match_date <= now:
            await callback.message.edit_text("⏰ Этот матч уже начался, изменить прогноз нельзя.")
            await callback.answer()
            return
        
        existing = session.query(Prediction).filter_by(
            user_id=user.id,
            match_id=match.id
        ).first()
        
        if not existing:
            await callback.message.edit_text("❌ У вас нет прогноза на этот матч.")
            await callback.answer()
            return
        
        text = f"✏️ *Изменить прогноз:*\n\n"
        text += f"{match.team1} ⚔️ {match.team2}\n"
        text += f"🕐 {match.match_date.strftime('%d.%m.%Y %H:%M') if match.match_date else 'Дата не указана'}\n\n"
        text += f"Текущий прогноз: *{existing.pred_score1}:{existing.pred_score2}*\n\n"
        text += "Выберите новый счёт:"
        
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
        
        # Проверяем, можно ли сделать/изменить прогноз
        if match.match_date and match.match_date <= now:
            await callback.message.edit_text("⏰ Этот матч уже начался, прогнозы недоступны.")
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
        
        action = "изменён" if existing else "сохранён"
        
        await callback.message.edit_text(
            f"✅ *Прогноз {action}!*\n\n"
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
    
