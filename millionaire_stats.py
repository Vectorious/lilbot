import time
import html


# TODO: we'll probably want to figure out a binary format to serialize these to. json is a little overkill.


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


class RoundResult:
    AnsweredIncorrectly = 0
    AnsweredCorrectly = 1
    Walked = 2


class MillionaireRound:
    def __init__(self, question, question_amount, lifelines_used, round_result):
        self.question = question
        self.question_amount = question_amount
        self.lifelines_used = lifelines_used
        self.round_result = round_result
    
    def serialize(self):
        return {
            'question': self.question.serialize(),
            'question_amount': self.question_amount,
            'lifelines_used': self.lifelines_used,
            'round_result': self.round_result,
        }
    
    @classmethod
    def deserialize(cls, ser_dict):
        question = Question.deserialize(ser_dict['question'])
        question_amount = ser_dict['question_amount']
        lifelines_used = ser_dict['lifelines_used']
        round_result = ser_dict['round_result']
        return cls(question, question_amount, lifelines_used, round_result)


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


def timestamp():
    return int(time.time())
