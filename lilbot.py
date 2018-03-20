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


class Question:
    # Example:
    # "category": "Science: Computers",
    # "type": "multiple",
    # "difficulty": "medium",
    # "question": "Moore&#039;s law originally stated that the number of transistors on a microprocessor chip would double every...",
    # "correct_answer": "Year",
    # "incorrect_answers": ["Four Years", "Two Years", "Eight Years"]
    def __init__(self):
        self.category = None
        self.type = None
        self.difficulty = None
        self.question = None
        self.correct_answer = None
        self.incorrect_answers = None

    @classmethod
    def deserialize(cls, ser_dict):
        question = cls()
        for name, value in ser_dict.items():
            if name != u'incorrect_answers':
                setattr(question, name, html.unescape(value))
            else:
                question.incorrect_answers = [html.unescape(answer) for answer in value]
        return question


ALPHABET = u'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
GLOBAL_STATE_PATH = 'global_state.json'
QUOTES_DIR = 'movie_quotes'
TRIVIA_PATH = 'trivia_movies.json'

BADMEME_BOT = discord.User(id=u'170903342199865344')

client = discord.Client()
imdb = Imdb()
seen_memes = Cache(10)

global_state = {
    'last_movie': None,
    'last_character': u'There is none.',
    'trivia_token': None,
}


def slugify(value):
    value = str(unicodedata.normalize('NFKD', value).encode('ascii', 'ignore'))
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


@command(u'!commands', u'List all commands associated with the bot.')
async def commands_command(message, rest):
    command_descriptions = [u'**{}** - *{}*'.format(usage or command_text, description) for command_text, _, description, usage in COMMANDS]
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
    dollar_amounts = [u'$500',
                      u'$1,000',
                      u'$2,000',
                      u'$3,000',
                      u'$5,000',
                      u'$7,000',
                      u'$10,000',
                      u'$20,000',
                      u'$30,000',
                      u'$50,000',
                      u'$100,000',
                      u'$250,000',
                      u'$500,000',
                      u'$1,000,000']
    checkpoints = [u'$5,000', u'$50,000', u'$1,000,000']

    fifty_fifty = u'!50/50'
    lifelines = [fifty_fifty]
    
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

    game_over = False
    walk_away_amount = u'$0'
    score = u'$0'
    for question_amount, question in zip(dollar_amounts, questions):
        if game_over:
            break
        answers = [question.correct_answer, *question.incorrect_answers]
        random.shuffle(answers)
        answers = [(letter, answer) for letter, answer in zip(ALPHABET, answers)]
        answer_key = dict(answers)

        def answer_key_text():
            return u'\n'.join([u'**{}.** {}'.format(letter, answer) for letter, answer in sorted(answer_key.items())])

        await client.send_message(message.channel, u'**{}**\n"{}"\n{}'.format(question_amount, question.question, answer_key_text()))

        def check(msg):
            if msg.author != player:
                return False
            lower_msg = msg.content.lower()
            if lower_msg.startswith(u'!walk'):
                return True
            if lower_msg[0].upper() in answer_key.keys() and (len(lower_msg) < 2 or not lower_msg[1].isalnum()):
                return True
            for lifeline in lifelines:
                if lower_msg.startswith(lifeline):
                    return True
            return False
        
        continuing = True
        while continuing:
            continuing = False
            response = await client.wait_for_message(timeout=120, channel=message.channel, check=check)
            if response:
                lower_msg = response.content.lower()
                if lower_msg.startswith(fifty_fifty):
                    lifelines.remove(fifty_fifty)
                    answers_to_remove = random.sample(question.incorrect_answers, 2)
                    for letter, answer in list(answer_key.items()):
                        if answer in answers_to_remove:
                            answer_key.pop(letter)
                    await client.send_message(message.channel, u'**Remaining answers:**\n{}'.format(answer_key_text()))
                    continuing = True
                elif lower_msg.startswith(u'!walk'):
                    score = walk_away_amount
                    game_over = True
                else:
                    if answer_key[lower_msg[0].upper()] == question.correct_answer:
                        await client.send_message(message.channel, u'**THAT IS CORRECT.**')
                        if question_amount in checkpoints:
                            score = question_amount
                        walk_away_amount = question_amount
                    else:
                        await client.send_message(message.channel, u'Wrong. The correct answer was **{}**.'.format(question.correct_answer))
                        game_over = True
            else:
                await client.send_message(message.channel, u'Time is up. The correct answer was **{}**.'.format(question.correct_answer))
                game_over = True
    await client.send_message(message.channel, u'{} walks away with {}.'.format(player, score))


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
