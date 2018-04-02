import time
import html


# NOTE: we'll probably want to replace this with something that keeps the original mapping intact
def two_way_map(mapping):
    mapping.update({value: key for key, value in mapping.items()})
    return mapping


def read_u32(byte_stream):
    return int.from_bytes(byte_stream.read(4), 'little', signed=False)


def read_i32(byte_stream):
    return int.from_bytes(byte_stream.read(4), 'little', signed=True)


def write_u32(byte_stream, n):
    byte_stream.write(n.to_bytes(4, 'little', signed=False))


def write_i32(byte_stream, n):
    byte_stream.write(n.to_bytes(4, 'little', signed=True))


def read_u8(byte_stream):
    return int.from_bytes(byte_stream.read(1), 'little', signed=False)

    
def write_u8(byte_stream, n):
    byte_stream.write(n.to_bytes(1, 'little', signed=False))


def read_i8(byte_stream):
    return int.from_bytes(byte_stream.read(1), 'little', signed=True)

    
def write_i8(byte_stream, n):
    byte_stream.write(n.to_bytes(1, 'little', signed=True))


def read_list(byte_stream, item_de_fn):
    length = read_u8(byte_stream)
    return [item_de_fn(byte_stream) for _ in range(length)]


def write_list(byte_stream, l):
    write_u8(byte_stream, len(l))
    if l:
        if isinstance(l[0], str):
            for item in l:
                write_string(byte_stream, item)
        else:
            for item in l:
                item.write(byte_stream)


def read_string(byte_stream):
    length = read_u8(byte_stream)
    bytes = byte_stream.read(length)
    return str(bytes, encoding='utf-8')


def write_string(byte_stream, s):
    str_bytes = bytes(s, encoding='utf-8')
    write_u8(byte_stream, len(str_bytes))
    byte_stream.write(str_bytes)


QUESTION_CATEGORY_MAP = two_way_map({
    9: u"General Knowledge",
    10: u"Entertainment: Books",
    11: u"Entertainment: Film",
    12: u"Entertainment: Music",
    13: u"Entertainment: Musicals & Theatres",
    14: u"Entertainment: Television",
    15: u"Entertainment: Video Games",
    16: u"Entertainment: Board Games",
    17: u"Science & Nature",
    18: u"Science: Computers",
    19: u"Science: Mathematics",
    20: u"Mythology",
    21: u"Sports",
    22: u"Geography",
    23: u"History",
    24: u"Politics",
    25: u"Art",
    26: u"Celebrities",
    27: u"Animals",
    28: u"Vehicles",
    29: u"Entertainment: Comics",
    30: u"Science: Gadgets",
    31: u"Entertainment: Japanese Anime & Manga",
    32: u"Entertainment: Cartoon & Animations",
})

QUESTION_TYPE_MAP = two_way_map({
    0: u"multiple",
    1: u"boolean",
})

QUESTION_DIFFICULTY_MAP = two_way_map({
    0: u"easy",
    1: u"medium",
    2: u"hard",
})

DOLLAR_AMOUNT_MAP = two_way_map({
    0: 500,
    1: 1000,
    2: 2000,
    3: 3000,
    4: 5000,
    5: 7000,
    6: 10000,
    7: 20000,
    8: 30000,
    9: 50000,
    10: 100000,
    11: 250000,
    12: 500000,
    13: 1000000,
})


class Lifeline:
    FiftyFifty = 0b0001
    DoubleDip = 0b0010


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
    
    def serialize(self):
        return {
            'category': self.category,
            'type': self.type,
            'difficulty': self.difficulty,
            'question': self.question,
            'correct_answer': self.correct_answer,
            'incorrect_answers': self.incorrect_answers,
        }

    @classmethod
    def deserialize(cls, ser_dict):
        question = cls()
        for name, value in ser_dict.items():
            if name != u'incorrect_answers':
                setattr(question, name, html.unescape(value))
            else:
                question.incorrect_answers = [html.unescape(answer) for answer in value]
        return question

    @classmethod
    def read(cls, byte_stream):
        question_obj = cls()
        question_obj.category = QUESTION_CATEGORY_MAP[read_u8(byte_stream)]
        question_obj.type = QUESTION_TYPE_MAP[read_u8(byte_stream)]
        question_obj.difficulty = QUESTION_DIFFICULTY_MAP[read_u8(byte_stream)]
        question_obj.question = read_string(byte_stream)
        question_obj.correct_answer = read_string(byte_stream)
        question_obj.incorrect_answers = read_list(byte_stream, read_string)
        return question_obj
    
    def write(self, byte_stream):
        write_u8(byte_stream, QUESTION_CATEGORY_MAP[self.category])
        write_u8(byte_stream, QUESTION_TYPE_MAP[self.type])
        write_u8(byte_stream, QUESTION_DIFFICULTY_MAP[self.difficulty])
        write_string(byte_stream, self.question)
        write_string(byte_stream, self.correct_answer)
        write_list(byte_stream, self.incorrect_answers)


class RoundResult:
    Walked = 0
    AnsweredCorrectly = 1
    AnsweredIncorrec = 2


class MillionaireRound:
    def __init__(self, question, question_amount, lifelines_used, given_answer, time_up=False):
        self.question = question
        self.question_amount = question_amount
        self.lifelines_used = lifelines_used
        self.given_answer = given_answer
        self.time_up = time_up
    
    @classmethod
    def deserialize(cls, ser_dict):
        question = Question.deserialize(ser_dict['question'])
        question_amount = ser_dict['question_amount']
        lifelines_used = ser_dict['lifelines_used']
        round_result = ser_dict['round_result']
        return cls(question, question_amount, lifelines_used, round_result)
    
    @classmethod
    def read(cls, byte_stream):
        question = Question.read(byte_stream)
        question_amount = DOLLAR_AMOUNT_MAP[read_u8(byte_stream)]
        lifelines_used = read_u8(byte_stream)
        given_answer_index = read_i8(byte_stream)
        time_up = False
        if given_answer_index == -1:
            given_answer = question.correct_answer
        elif given_answer_index >= 0:
            given_answer = question.incorrect_answers[given_answer_index]
        else:
            given_answer = None
            if given_answer_index == -2:
                time_up = True
        return cls(question, question_amount, lifelines_used, given_answer, time_up)
    
    def write(self, byte_stream):
        self.question.write(byte_stream)
        write_u8(byte_stream, DOLLAR_AMOUNT_MAP[self.question_amount])
        write_u8(byte_stream, self.lifelines_used)
        if self.given_answer == self.question.correct_answer:
            write_i8(byte_stream, -1)
        elif self.time_up:
            write_i8(byte_stream, -2)
        elif self.given_answer is None:
            write_i8(byte_stream, -3)
        else:
            write_i8(byte_stream, self.question.incorrect_answers.index(self.given_answer))


class MillionaireGame:
    def __init__(self, user, lifelines, rounds, timestamp, amount_earned):
        self.user = user
        self.lifelines = lifelines
        self.rounds = rounds
        self.timestamp = timestamp
        self.amount_earned = amount_earned

    def serialize(self):
        return {
            'user': self.user,
            'lifelines': self.lifelines,
            'rounds': [round.serialize() for round in self.rounds],
            'timestamp': self.timestamp,
            'amount_earned': self.amount_earned,
        }

    @classmethod
    def deserialize(cls, ser_dict):
        user = ser_dict['user']
        lifelines = ser_dict['lifelines']
        rounds = [MillionaireRound.deserialize(round) for round in ser_dict['rounds']]
        timestamp = ser_dict['timestamp']
        amount_earned = ser_dict['amount_earned']
        return cls(user, lifelines, rounds, timestamp, amount_earned)
    
    @classmethod
    def read(cls, byte_stream):
        user = read_string(byte_stream)
        lifelines = read_u8(byte_stream)
        rounds = read_list(byte_stream, MillionaireRound.read)
        timestamp = read_u32(byte_stream)
        amount_earned = read_i32(byte_stream)
        return cls(user, lifelines, rounds, timestamp, amount_earned)
    
    def write(self, byte_stream):
        write_string(byte_stream, self.user)
        write_u8(byte_stream, self.lifelines)
        write_list(byte_stream, self.rounds)
        write_u32(byte_stream, self.timestamp)
        write_i32(byte_stream, self.amount_earned)


def timestamp():
    return int(time.time())
