import asyncio
import html
import json
import os
import random
import re
import time
import unicodedata

import discord
import requests
from imdbpie import Imdb

from millionaire_stats import *


class Cache:
    def __init__(self, size):
        self.items = []
        self.size = size

    def push(self, item):
        self.items.insert(0, item)
        while len(self.items) > self.size:
            self.items.pop()

    def __contains__(self, item):
        return item in self.items


class TimeCache:
    def __init__(self, invalidate_time):
        self.invalidate_time = invalidate_time
        self.items = {}
        self.items_updated = {}

    def get(self, key, d=None):
        age = self.age(key)
        if age:
            if age < self.invalidate_time:
                return self.items[key]
            else:
                print('"{}" invalidated, last updated {} seconds ago.')
                return d
        else:
            return d
    
    def __getitem__(self, key):
        age = self.age(key)
        if age:
            if age < self.invalidate_time:
                return self.items[key]
            else:
                print('"{}" invalidated, last updated {} seconds ago.')
                # NOTE: just treat as normal KeyError. only raising exception here for consistency
                raise KeyError()
        else:
            raise KeyError()
    
    def __setitem__(self, key, value):
        self.items[key] = value
        self.items_updated[key] = time.time()

    def age(self, key):
        updated = self.items_updated.get(key, None)
        if updated:
            now = time.time()
            return int(now - updated)
        else:
            return None
    
    def __contains__(self, item):
        return item in self.items


class Quote:
    def __init__(self, text, character, movie):
        self.text = text
        self.character = character
        self.movie = movie

    def __bool__(self):
        return True


class Movie:
    def __init__(self, title, quotes):
        self.title = title
        for quote in quotes:
            quote.movie = self
        self.quotes = quotes

    def serialize(self):
        quotes = [
            {
                'text': quote.text,
                'character': quote.character,
            } for quote in self.quotes
        ]
        return {
            'title': self.title,
            'quotes': quotes,
        }

    @classmethod
    def deserialize(cls, ser_dict):
        quotes = [Quote(quote['text'], quote['character'], None) for quote in ser_dict['quotes']]
        return cls(ser_dict['title'], quotes)


ALPHABET = u'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
GLOBAL_STATE_PATH = 'global_state.json'
QUOTES_DIR = 'movie_quotes'
MILLIONAIRE_STATS_DIR = 'millionaire_stats'
TRIVIA_PATH = 'trivia_movies.json'

BADMEME_BOT = discord.User(id=u'170903342199865344')
TIME_CACHE = TimeCache(5 * 60)

NAME_CACHE = {}

client = discord.Client()
imdb = Imdb()
seen_memes = Cache(10)

global_state = {
    'last_movie': None,
    'last_character': u'There is none.',
    'trivia_token': None,
    'trivia_leaderboard': {},
}


async def get_discord_name(user_id):
    name = NAME_CACHE.get(user_id, None)
    if name:
        return name
    else:
        name = str(await client.get_user_info(user_id))
        NAME_CACHE[user_id] = name
        return name


def slugify(value):
    value = str(unicodedata.normalize('NFKD', value).encode('ascii', 'ignore'), encoding='ascii')
    value = re.sub(r'[^\w\s-]', '', value).strip().lower()
    value = re.sub(r'[-\s]+', '-', value)
    return value


def get_movie_filenames():
    try:
        return next(os.walk(QUOTES_DIR))[2]
    except OSError:
        return None


def load_movie(title):
    if not title:
        return None
    if title.endswith('.json'):
        path = title
        if not path.startswith(QUOTES_DIR):
            path = os.path.join(QUOTES_DIR, path)
    else:
        filename = slugify(title) + '.json'
        path = os.path.join(QUOTES_DIR, filename)
    try:
        return Movie.deserialize(json.load(open(path, 'r', encoding='utf-8')))
    except IOError:
        return None


def save_movie(movie):
    try:
        os.mkdir('movie_quotes')
    except OSError:
        pass
    filename = slugify(movie.title) + '.json'
    path = os.path.join(QUOTES_DIR, filename)
    json.dump(movie.serialize(), open(path, 'w', encoding='utf-8'))


def extract_quote(quote):
    keys = ('text', 'stageDirection')
    for key in keys:
        for line in quote:
            result = line.get(key, None)
            if result:
                character = line.get('characters', None)
                if character:
                    character = character[0]['character']
                return Quote(result, character, None)
    return None


def load_trivia_movies():
    path = TRIVIA_PATH
    try:
        return json.load(open(path, 'r', encoding='utf-8'))
    except IOError:
        return []


def save_trivia_movies(movies):
    path = TRIVIA_PATH
    json.dump(movies, open(path, 'w', encoding='utf-8'))


def get_movie(movie_title):
    if not movie_title:
        return None
    movie = load_movie(movie_title)
    if not movie:
        results = imdb.search_for_title(movie_title)
        if results:
            result = results[0]
            movie_title = result['title']
            movie = load_movie(movie_title)
            if not movie:
                try:
                    quote_results = imdb.get_title_quotes(result['imdb_id'])['quotes']
                    movie = [extract_quote(movie['lines']) for movie in quote_results if count_lines(movie['lines']) <= 1]
                    movie = Movie(movie_title, [quote for quote in movie if quote])
                    save_movie(movie)
                except LookupError:
                    movie = None
    return movie


def get_quote(movie):
    if movie.quotes:
        quote = random.choice(movie.quotes)
        global_state['last_character'] = quote.character
        global_state['last_movie'] = movie
        save_global_state()
        return quote
    return None


def count_lines(quote):
    count = 0
    for line in quote:
        if 'text' in line:
            count += 1
    return count


def get_session_token():
    url = 'https://opentdb.com/api_token.php?command=request'
    response = requests.get(url).json()
    if response[u'response_code'] == 0:
        return response[u'token']
    return None


def get_questions(amount, category=None, difficulty=None, session_token=None):
    if not session_token:
        session_token = global_state['trivia_token']
    if not session_token:
        session_token = get_session_token()
        if session_token:
            global_state['trivia_token'] = session_token
            save_global_state()
    url = 'https://opentdb.com/api.php?amount={}&type=multiple{}{}{}'.format(
        amount,
        '&category={}'.format(category) if category else '',
        '&difficulty={}'.format(difficulty) if difficulty else '',
        '&token={}'.format(session_token) if session_token else '')
    response = requests.get(url).json()

    # Code 0: Success Returned results successfully.
    # Code 1: No Results Could not return results. The API doesn't have enough questions for your query. (Ex. Asking for 50 Questions in a Category that only has 20.)
    # Code 2: Invalid Parameter Contains an invalid parameter. Arguements passed in aren't valid. (Ex. Amount = Five)
    # Code 3: Token Not Found Session Token does not exist.
    # Code 4: Token Empty Session Token has returned all possible questions for the specified query. Resetting the Token is necessary.

    if response[u'response_code'] == 0:
        return [Question.deserialize(question) for question in response[u'results']]
    elif response[u'response_code'] in (3, 4):
        session_token = get_session_token()
        if session_token:
            return get_questions(amount, category, difficulty, session_token)
        else:
            # can't get new session token, invalidate old session token
            global_state['trivia_token'] = None
            save_global_state()
    return None


def get_categories():
    url = 'https://opentdb.com/api_category.php'
    response = requests.get(url).json()
    return [(category[u'id'], category[u'name']) for category in response[u'trivia_categories']]


def how_long_ago(seconds):
    minutes = seconds // 60
    hours = minutes // 60
    days = hours // 24
    if days > 0:
        unit = u'days' if days > 1 else u'day'
        return u'{} {} ago'.format(days, unit)
    if hours > 0:
        unit = u'hours' if hours > 1 else u'hour'
        return u'{} {} ago'.format(hours, unit)
    if minutes > 0:
        unit = u'minutes' if minutes > 1 else u'minute'
        return u'{} {} ago'.format(minutes, unit)
    return u'just now'


def int_to_dollars(n):
    return u'${:,}'.format(n)


def load_global_state():
    try:
        state = json.load(open(GLOBAL_STATE_PATH, 'r', encoding='utf-8'))
    except IOError:
        return
    state['last_movie'] = get_movie(state['last_movie'])
    global_state.update(state)


def save_global_state():
    state = dict(global_state)
    if state['last_movie']:
        state['last_movie'] = global_state['last_movie'].title
    json.dump(state, open(GLOBAL_STATE_PATH, 'w', encoding='utf-8'))


def get_millionaire_game_filenames():
    return next(os.walk(MILLIONAIRE_STATS_DIR))[2]


def load_millionaire_games(user_id, deserialize=True):
    if isinstance(user_id, str) and user_id.endswith('.json'):
        if user_id.startswith(MILLIONAIRE_STATS_DIR):
            path = user_id
        else:
            path = os.path.join(MILLIONAIRE_STATS_DIR, user_id)
    else:
        path = os.path.join(MILLIONAIRE_STATS_DIR, '{}.json'.format(user_id))
    try:
        games = json.load(open(path, 'r', encoding='utf-8'))
    except FileNotFoundError:
        return None
    if deserialize:
        return [MillionaireGame.deserialize(game) for game in games]
    else:
        return games


def save_millionaire_game(game):
    path = os.path.join(MILLIONAIRE_STATS_DIR, '{}.json'.format(game.user))
    games = load_millionaire_games(path, deserialize=False)
    if not games:
        games = []
    games.append(game.serialize())
    temp_ext = '.temp'
    temp_path = path + temp_ext
    json.dump(games, open(temp_path, 'w', encoding='utf-8'))
    os.replace(temp_path, path)


@client.event
async def on_ready():
    load_global_state()
    print('Logged in as')
    print(client.user.name)
    print(client.user.id)
    print('------')


COMMANDS = []
def command(command_string, description, usage=None):
    def decorator(func):
        COMMANDS.append((command_string, func, description, usage))
        return func
    return decorator


@command(u'!quote', u'Get a random quote.', usage=u'!quote [title]')
async def quote_command(message, rest):
    if rest:
        movie = get_movie(rest)
    else:
        movie_filenames = get_movie_filenames()
        if movie_filenames:
            movie = get_movie(random.choice(movie_filenames))
        else:
            movie = None

    if movie:
        quote = get_quote(movie)
        if quote:
            await client.send_message(message.channel, u'"{}"'.format(quote.text))
        else:
            await client.send_message(message.channel, u'No quotes available for "{}".'.format(movie.text))
    else:
        await client.send_message(message.channel, u'No results found.')


@command(u'!title', u'Get the title that the last quote was from.')
async def title_command(message, rest):
    movie = global_state['last_movie']
    if movie and movie.quotes:
        await client.send_message(message.channel, movie.title)
    else:
        await client.send_message(message.channel, u'There is none.')


@command(u'!character', u'Get the character that the last quote was from.')
async def character_command(message, rest):
    if global_state['last_character']:
        await client.send_message(message.channel, global_state['last_character'])
    else:
        await client.send_message(message.channel, u'No character available.')


@command(u'!another', u'Get another quote from the last title.')
async def another_command(message, rest):
    movie = global_state['last_movie']
    if movie:
        quote = get_quote(movie)
        await client.send_message(message.channel, u'"{}"'.format(quote.text))
        global_state['last_character'] = quote.character
        save_global_state()
    else:
        await client.send_message(message.channel, u'Nothing to nother.')


@command(u'!qtrivia add', u'Add a new title to the quote trivia list.', usage=u'!qtrivia add <title>')
async def qtrivia_add_command(message, rest):
    movie = get_movie(rest)
    if movie:
        movies = load_trivia_movies()
        if movie.title in movies:
            await client.send_message(message.channel, u'*{}* already added to trivia.'.format(movie.title))
        else:
            movies.append(movie.title)
            save_trivia_movies(movies)
            await client.send_message(message.channel, u'Added *{}* to trivia.'.format(movie.title))
    else:
        await client.send_message(message.channel, u'No results found.')


@command(u'!qtrivia clear', u'Clear the quote trivia list.')
async def qtrivia_clear_command(message, rest):
    save_trivia_movies([])


@command(u'!qtrivia remove', u'Remove a title from the quote trivia list.', usage=u'!qtrivia remove <title>')
async def qtrivia_remove_command(message, rest):
    movie_title_slug = slugify(rest.strip())
    if movie_title_slug:
        trivia_movie_titles = load_trivia_movies()
        for index in range(len(trivia_movie_titles)):
            trivia_movie_title = trivia_movie_titles[index]
            if slugify(trivia_movie_title) == movie_title_slug:
                removed_title = trivia_movie_titles.pop(index)
                save_trivia_movies(trivia_movie_titles)
                await client.send_message(message.channel, u'*{}* removed from trivia.'.format(removed_title))
                break
        else:
            await client.send_message(message.channel, u'No matches for title "{}".'.format(movie_title_slug))


@command(u'!qtrivia list', u'List the titles currently in the quote trivia list.')
async def qtrivia_list_command(message, rest):
    movie_titles = load_trivia_movies()
    if movie_titles:
        await client.send_message(message.channel, u'; '.join([u'*{}*'.format(title) for title in movie_titles]))
    else:
        await client.send_message(message.channel, u'Empty.')


@command(u'!qtrivia', u'Play quote trivia.', usage=u'!qtrivia [<amount> [title]]')
async def qtrivia_command(message, rest):
    movie_lock = None
    count = 1

    try:
        n, *rest = rest.strip().split(None, 1)
        count = int(n)
        if rest:
            movie_lock = get_movie(rest[0])
    except:
        pass

    scores = {}
    quotes = []
    if movie_lock:
        quotes = movie_lock.quotes
    else:
        movies = load_trivia_movies()
        for movie_title in movies:
            movie = get_movie(movie_title)
            if movie:
                quotes.extend([quote for quote in movie.quotes if quote.character])
            else:
                await client.send_message(message.channel, u'Error: could not retrieve *{}*.'.format(movie_title))
    
    count = min([count, len(quotes)])
    quotes = random.sample(quotes, count)

    for question_number, quote in enumerate(quotes):
        prefix = u'' if count <= 1 else u'#{}  '.format(question_number + 1)
        await client.send_message(message.channel, u'**{}_{}_**\n"{}"'.format(prefix, quote.movie.title, quote.text))
        question_number += 1

        def check(msg):
            return msg.author != client.user and (msg.content.startswith(u'!stop') or (slugify(quote.character.strip()) in slugify(msg.content.strip())))
        
        response = await client.wait_for_message(timeout=30, channel=message.channel, check=check)

        if response:
            if response.content.startswith(u'!stop'):
                await client.send_message(message.channel, u'k.')
                break
            else:
                await client.send_message(message.channel, u'{} got it. **{}** - *{}*.'.format(response.author, quote.character, quote.movie.title))
                scores[response.author] = scores.get(response.author, 0) + 1

        else:
            await client.send_message(message.channel, u'Noobs. **{}** - *{}*.'.format(quote.character, quote.movie.title))

    if scores and count > 1:
        await client.send_message(message.channel, u', '.join([u'{}: {}'.format(name, score) for name, score in scores.items()]))


@command(u'!count', u'Get the amount of quotes a title has.', usage=u'!count <title>')
async def count_command(message, rest):
    movie = get_movie(rest)
    if movie:
        await client.send_message(message.channel, u'*{}* has **{}** quotes.'.format(movie.title, len(movie.quotes)))
    else:
        await client.send_message(message.channel, u'No results found.')


@command(u'!help', u'List all commands associated with the bot.')
async def help_command(message, rest):
    command_descriptions = [u'**`{}`** - *{}*'.format(usage or command_text, description) for command_text, _, description, usage in COMMANDS]
    await client.send_message(message.channel, u'\n'.join(command_descriptions))


@command(u'!trivia', u'Play trivia.', usage=u'!trivia [<amount> [category]]')
async def trivia_command(message, rest):
    category = None
    amount = 1

    try:
        n, *rest = rest.strip().split(None, 1)
        amount = int(n)
        if rest:
            category = rest[0]
    except:
        pass

    questions = get_questions(amount, category)
    if not questions:
        await client.send_message(message.channel, u'Unable to retrieve questions.')

    scores = {}
    for question_number, question in enumerate(questions):
        answers = [question.correct_answer, *question.incorrect_answers]
        random.shuffle(answers)
        answer_key = {letter: answer for letter, answer in zip(ALPHABET, answers)}
        answer_key_text = u'\n'.join([u'**{}.** {}'.format(letter, answer) for letter, answer in zip(ALPHABET, answers)])

        prefix = u'' if amount <= 1 else u'#{}  '.format(question_number + 1)
        await client.send_message(message.channel, u'**{}_{}_ ({})**\n"{}"\n{}'.format(prefix, question.category, question.difficulty, question.question, answer_key_text))
        question_number += 1

        def check(msg):
            if msg.author != client.user and msg.content[0].upper() in answer_key.keys():
                if len(msg.content) > 1 and msg.content[1].isalnum():
                    return False
                return True
            return False
        
        response = await client.wait_for_message(timeout=30, channel=message.channel, check=check)

        if response:
            if response.content.startswith(u'!stop'):
                await client.send_message(message.channel, u'k.')
                break
            else:
                if answer_key[response.content[0].upper()] == question.correct_answer:
                    await client.send_message(message.channel, u'{} got it. **{}**.'.format(response.author, question.correct_answer))
                    scores[response.author] = scores.get(response.author, 0) + 1
                else:
                    await client.send_message(message.channel, u'Wrong. **{}**.'.format(question.correct_answer))
        else:
            await client.send_message(message.channel, u'Noobs. **{}**.'.format(question.correct_answer))
        await asyncio.sleep(2)

    if scores and amount > 1:
        await client.send_message(message.channel, u', '.join([u'{}: {}'.format(name, score) for name, score in scores.items()]))


@command(u'!millionaire', u'Play _Who Wants to be a Millionaire!_')
async def millionaire_command(message, rest):
    player = message.author
    await client.send_message(message.channel, u'**{}, welcome to _Who Wants to be a Millionaire!_**'.format(player))
    await client.send_typing(message.channel)

    dollar_amounts = [500,
                      1000,
                      2000,
                      3000,
                      5000,
                      7000,
                      10000,
                      20000,
                      30000,
                      50000,
                      100000,
                      250000,
                      500000,
                      1000000]
    checkpoints = [5000, 50000, 1000000]


    lifelines = Lifeline.FiftyFifty | Lifeline.DoubleDip
    lifeline_key = {
        Lifeline.FiftyFifty: u'!50/50',
        Lifeline.DoubleDip: u'!dd',
    }
    
    question_sets = [(5, 'easy'), (5, 'medium'), (4, 'hard')]
    questions = []
    for amount, difficulty in question_sets:
        diff_questions = None
        for attempt in range(3):
            diff_questions = get_questions(amount, difficulty=difficulty)
            if diff_questions:
                break
            await client.send_message(message.channel, u'Unable to retrieve questions, please wait... ({}/3)'.format(attempt + 1))
            await asyncio.sleep(10)
        else:
            await client.send_message(message.channel, u'Unable to generate game. Try again later.')
            return
        questions.extend(diff_questions)

    if player.voice:
        try:
            voice = await client.join_voice_channel(player.voice.voice_channel)
        except:
            voice = None
        if voice:
            audio_question = voice.create_ffmpeg_player('question.mp3')
            audio_question.start()
    else:
        voice = None

    game_over = False
    stats_rounds = []
    stats = MillionaireGame(player.id, lifelines, stats_rounds, timestamp(), None)
    walk_away_amount = 0
    score = 0
    for question_amount, question in zip(dollar_amounts, questions):
        if game_over:
            break
        lifelines_used = 0
        answers = [question.correct_answer, *question.incorrect_answers]
        random.shuffle(answers)
        answers = [(letter, answer) for letter, answer in zip(ALPHABET, answers)]
        answer_key = dict(answers)

        stats_round = MillionaireRound(question, question_amount, None, None)

        def answer_key_text():
            return u'\n'.join([u'**{}.** {}'.format(letter, answer) for letter, answer in sorted(answer_key.items())])

        await client.send_message(message.channel, u'**${:,}**\n"{}"\n{}'.format(question_amount, question.question, answer_key_text()))

        def check(msg):
            if msg.author != player:
                return False
            lower_msg = msg.content.lower()
            if lower_msg.startswith(u'!walk'):
                return True
            if lower_msg[0].upper() in answer_key.keys() and (len(lower_msg) < 2 or not lower_msg[1].isalnum()):
                return True
            for lifeline, lifeline_command in lifeline_key.items():
                if lifeline & lifelines and lower_msg.startswith(lifeline_command):
                    if Lifeline.DoubleDip & lifelines_used or ((lifeline & Lifeline.DoubleDip) and lifelines_used):
                        return False
                    else:
                        return True
            return False


        continuing = True
        while continuing:
            continuing = False
            response = await client.wait_for_message(timeout=120, channel=message.channel, check=check)
            if response:
                lower_msg = response.content.lower()
                if lower_msg.startswith(lifeline_key[Lifeline.FiftyFifty]):
                    lifelines ^= Lifeline.FiftyFifty
                    lifelines_used |= Lifeline.FiftyFifty
                    answers_to_remove = random.sample(question.incorrect_answers, 2)
                    for letter, answer in list(answer_key.items()):
                        if answer in answers_to_remove:
                            answer_key.pop(letter)
                    await client.send_message(message.channel, u'**Remaining answers:**\n{}'.format(answer_key_text()))
                    continuing = True
                elif lower_msg.startswith(lifeline_key[Lifeline.DoubleDip]):
                    lifelines ^= Lifeline.DoubleDip
                    lifelines_used |= Lifeline.DoubleDip
                    response = await client.wait_for_message(timeout=120, channel=message.channel, check=check)
                    if response:
                        if answer_key[response.content[0].upper()] == question.correct_answer:
                            await client.send_message(message.channel, u'**THAT IS CORRECT.**')
                            stats_round.round_result = RoundResult.AnsweredCorrectly
                            if question_amount in checkpoints:
                                score = question_amount
                            walk_away_amount = question_amount
                        else:
                            await client.send_message(message.channel, u"I'm sorry... that is incorrect. You have one more shot at the prize.")
                            answer_key.pop(response.content[0].upper())
                            await client.send_message(message.channel, u'**Remaining answers:**\n{}'.format(answer_key_text()))
                            response = await client.wait_for_message(timeout=120, channel=message.channel, check=check)
                            if response:
                                if answer_key[response.content[0].upper()] == question.correct_answer:
                                    await client.send_message(message.channel, u'**THAT IS CORRECT.**')
                                    stats_round.round_result = RoundResult.AnsweredCorrectly
                                    if question_amount in checkpoints:
                                        score = question_amount
                                    walk_away_amount = question_amount
                                else:
                                    await client.send_message(message.channel, u'Wrong. The correct answer was **{}**.'.format(question.correct_answer))
                                    stats_round.round_result = RoundResult.AnsweredIncorrectly
                                    game_over = True
                            else:
                                await client.send_message(message.channel, u'Time is up. The correct answer was **{}**.'.format(question.correct_answer))
                                stats_round.round_result = RoundResult.AnsweredIncorrectly
                                game_over = True
                    else:
                        await client.send_message(message.channel, u'Time is up. The correct answer was **{}**.'.format(question.correct_answer))
                        stats_round.round_result = RoundResult.AnsweredIncorrectly
                        game_over = True
                elif lower_msg.startswith(u'!walk'):
                    score = walk_away_amount
                    await client.send_message(message.channel, u'I respect that. The correct answer was **{}** by the way.'.format(question.correct_answer))
                    stats_round.round_result = RoundResult.Walked
                    game_over = True
                else:
                    if answer_key[lower_msg[0].upper()] == question.correct_answer:
                        await client.send_message(message.channel, u'**THAT IS CORRECT.**')
                        stats_round.round_result = RoundResult.AnsweredCorrectly
                        if question_amount in checkpoints:
                            score = question_amount
                        walk_away_amount = question_amount
                    else:
                        await client.send_message(message.channel, u'Wrong. The correct answer was **{}**.'.format(question.correct_answer))
                        stats_round.round_result = RoundResult.AnsweredIncorrectly
                        game_over = True
            else:
                await client.send_message(message.channel, u'Time is up. The correct answer was **{}**.'.format(question.correct_answer))
                stats_round.round_result = RoundResult.AnsweredIncorrectly
                game_over = True
        stats_round.lifelines_used = lifelines_used
        stats.rounds.append(stats_round)
    await client.send_message(message.channel, u'{} walks away with ${:,}.'.format(player, score))
    stats.amount_earned = score
    save_millionaire_game(stats)


@command(u'!leaderboard', u'Display _Who Wants to be a Millionaire!_ leaderboard.')
async def leaderboard_command(message, rest):
    leaderboard = TIME_CACHE.get('leaderboard', None)
    format_str = u'`{:<20}{:>19}{:>18}{:>17}`'
    if not leaderboard:
        await client.send_typing(message.channel)
        player_scores = []
        # Name, Total Earnings, Highest Score, Games Played
        for user_filename in get_millionaire_game_filenames():
            games = load_millionaire_games(user_filename)
            if not games:
                continue
            name = await get_discord_name(games[0].user)
            highest_earned = 0
            total_earned = 0
            count = 0
            for game in games:
                total_earned += game.amount_earned
                highest_earned = max([highest_earned, game.amount_earned])
                count += 1
            player_scores.append((name, highest_earned, total_earned, count))
        player_scores.sort(key=lambda item: item[3], reverse=True) # sort by total earned
        # TODO: fix printing dollar amounts here
        leaderboard_builder = [format_str.format(*player_score) for player_score in player_scores]
        leaderboard_builder.insert(0, u'**' + format_str.format(u'Name', u'Total Earnings', u'Highest Score', u'Games Played') + u'**')
        leaderboard = u'\n'.join(leaderboard_builder)
        TIME_CACHE['leaderboard'] = leaderboard
    updated_str = u'\n*(Last updated {})*'.format(how_long_ago(TIME_CACHE.age('leaderboard')))
    await client.send_message(message.channel, leaderboard + updated_str)


@command(u'!fff', u'Play _Fastest Finger First_ to determine who gets to play _Millionaire!_')
async def fff_command(message, rest):
    await client.send_typing(message.channel)
    question = get_questions(1)
    if question:
        question = question[0]
        
        answers = [question.correct_answer, *question.incorrect_answers]
        random.shuffle(answers)
        answers = [(letter, answer) for letter, answer in zip(ALPHABET, answers)]
        answer_key = dict(answers)

        def answer_key_text():
            return u'\n'.join([u'**{}.** {}'.format(letter, answer) for letter, answer in sorted(answer_key.items())])

        answered = set()

        await client.send_message(message.channel, u'**Fastest Finger First**\n"{}"\n{}'.format(question.question, answer_key_text()))

        def check(msg):
            if msg.author == client.user or msg.author in answered:
                return False
            upper_msg = msg.content.upper()
            if upper_msg[0] in answer_key.keys() and (len(upper_msg) < 2 or not upper_msg[1].isalnum()):
                if answer_key[upper_msg[0]] == question.correct_answer:
                    return True
                else:
                    answered.add(msg.author)
            return False
        
        response = await client.wait_for_message(timeout=30, channel=message.channel, check=check)

        if response:
            await millionaire_command(response, '')
        else:
            await client.send_message(message.channel, u'Time is up. The correct answer was **{}**.'.format(question.correct_answer))
    else:
        await client.send_message(message.channel, u'Unable to retrieve question.')


@command(u'!categories', u'List all available trivia categories.')
async def categories_command(message, rest):
    categories = get_categories()
    category_text = u'\n'.join(['**{}**: *{}*'.format(id, name) for id, name in categories])
    await client.send_message(message.channel, category_text)


@command(u'!source', u'Get a link to the GitHub repository.')
async def source_command(message, rest):
    await client.send_message(message.channel, u'https://github.com/Vectorious/lilbot')


@command(u'.', u'Remove after @NotSoBot responds and delete duplicate ".badmeme" responses.', usage=u'.[@NotSoBot command]')
async def badmeme_bot_command(message, rest):
    response = await client.wait_for_message(timeout=10, author=BADMEME_BOT, channel=message.channel)
    await client.delete_message(message)
    if message.content.startswith(u'.badmeme') and response:
        if response.content in seen_memes:
            await client.delete_message(response)
        else:
            seen_memes.push(response.content)


@client.event
async def on_message(message):
    if message.author != client.user:
        for command_text, func, *_ in COMMANDS:
            if message.content.startswith(command_text):
                await func(message, message.content[len(command_text):])
                break


def main():
    try:
        token = open('token.txt', 'r', encoding='utf-8').read().strip()
    except IOError:
        pass
    if token:
        client.run(token)
    else:
        print('Please supply a "token.txt".')


if __name__ == '__main__':
    main()
