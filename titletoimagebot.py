#!/usr/bin/env python3

"""meh"""

__version__ = '0.6.2'
__author__ = 'gerenook'

import argparse
import json
import logging
import re
import sqlite3
import sys
import time
from io import BytesIO
from logging.handlers import TimedRotatingFileHandler
from os import remove

import praw
import requests
from imgurpython import ImgurClient
from imgurpython.helpers.error import (ImgurClientError,
                                       ImgurClientRateLimitError)
from PIL import Image, ImageDraw, ImageFont
from prawcore.exceptions import RequestException, ResponseException

import apidata


class RedditImage:
    """RedditImage class

    :param image: the image
    :type image: PIL.Image.Image
    """
    margin = 10
    font_file = 'seguiemj.ttf'
    font_scale_factor = 20
    regex_resolution = re.compile(r'\s?\[[0-9]+\s?[xX*×]\s?[0-9]+\]')

    def __init__(self, image):
        self._image = image
        self._width, self._height = image.size
        self._font_title = ImageFont.truetype(
            RedditImage.font_file,
            self._width // RedditImage.font_scale_factor
        )
        self._font_author = ImageFont.truetype(
            RedditImage.font_file,
            self._width // (RedditImage.font_scale_factor*2)
        )

    def _split_title(self, title, boot):
        """Split title into multiple lines

        :param title: the title to split
        :type title: str
        :param boot: if True, split title on [',', ';', '.'], else wrap text
        :type boot: bool
        :returns: split title
        :rtype: list
        """
        if boot:
            all_delimiters = [',', ';', '.']
            delimiter = None
            new = ['']
            i = 0
            for character in title:
                if character == ' ' and not new[i]:
                    continue
                new[i] += character
                if not delimiter:
                    if character in all_delimiters:
                        delimiter = character
                if character == delimiter:
                    new.append('')
                    i += 1
            # quick fix
            for n in new:
                if self._font_title.getsize(n)[0] + RedditImage.margin > self._width:
                    delimiter = None
            if delimiter:
                return [n for n in new if n]
        # no boot, wrap text
        new = ['']
        line_words = []
        words = title.split()
        for word in words:
            line_words.append(word)
            new[-1] = ' '.join(line_words)
            if self._font_title.getsize(new[-1])[0] + RedditImage.margin > self._width:
                new[-1] = new[-1][:-len(word)].strip()
                new.append(word)
                line_words = [word]
        # remove empty strings
        return [n for n in new if n]

    def add_title(self, title, boot):
        """Add title to new whitespace on image

        :param title: the title to add
        :type title: str
        :param boot: if True, split title on [',', ';', '.'], else wrap text
        :type boot: bool
        """
        # remove resolution appended to title (e.g. '<title> [1000 x 1000]')
        title = RedditImage.regex_resolution.sub('', title)
        line_height = self._font_title.getsize(title)[1] + RedditImage.margin
        texts = self._split_title(title, boot)
        author_height = self._font_author.getsize('/')[1] + RedditImage.margin
        whitespace_height = (line_height * len(texts)) + author_height
        new = Image.new('RGB', (self._width, self._height + whitespace_height), '#fff')
        new.paste(self._image, (0, whitespace_height))
        draw = ImageDraw.Draw(new)
        for i, text in enumerate(texts):
            draw.text((RedditImage.margin, i * line_height + author_height),
                      text, '#000', self._font_title)
        self._width, self._height = new.size
        self._image = new

    def add_author(self, author):
        """Add /u/author to top right of image

        :param author: the author to add (without /u/)
        :type author: str
        """
        text = '/u/' + author
        draw = ImageDraw.Draw(self._image)
        text_width = self._font_author.getsize(text)[0]
        pos = (self._width - text_width - RedditImage.margin, RedditImage.margin // 2)
        draw.text(pos, text, '#000', self._font_author)

    def upload(self, imgur, config):
        """Upload self._image to imgur

        :param imgur: the imgur api client
        :type imgur: imgurpython.client.ImgurClient
        :param config: imgur image config
        :type config: dict
        :returns: imgur url if upload successful, else None
        :rtype: str
        """
        path_png = 'temp.png'
        path_jpg = 'temp.jpg'
        self._image.save(path_png)
        self._image.save(path_jpg)
        try:
            response = imgur.upload_from_path(path_png, config, anon=False)
        except ImgurClientError as error:
            logging.warning('png upload failed, trying jpg | %s', error)
            try:
                response = imgur.upload_from_path(path_jpg, config, anon=False)
            except ImgurClientError as error:
                logging.error('jpg upload failed, returning | %s', error)
                return None
        except ImgurClientRateLimitError:
            raise
        finally:
            remove(path_png)
            remove(path_jpg)
        return response['link']


class TitleToImageBot:
    """TitleToImageBot class

    :param subreddit: the subreddit(s) to process, can be concatenated with +
    :type subreddit: str
    """
    def __init__(self, subreddit):
        self._sql_connection = sqlite3.connect('database.db')
        self._sql = self._sql_connection.cursor()
        self._reddit = praw.Reddit(**apidata.reddit)
        self._subreddit = self._reddit.subreddit(subreddit)
        self._imgur = ImgurClient(**apidata.imgur)
        self._template = (
            '[Image with added title]({image_url})\n\n'
            '---\n\n'
            '^summon ^me ^with ^/u/titletoimagebot ^| '
            '^[remove](https://reddit.com/message/compose/'
            '?to=TitleToImageBot&subject=remove&message={comment_id}) '
            '^\\(for ^OP\\) ^| '
            '^[feedback](https://reddit.com/message/compose/'
            '?to=TitleToImageBot&subject=feedback%20{submission_id}) ^| '
            '^[source](https://github.com/gerenook/titletoimagebot/'
            'blob/master/titletoimagebot.py)'
        )

    def _reply_imgur_url(self, url, submission, source_comment):
        """doc todo

        :param url: -
        :type url: str
        :param submission: -
        :type submission: -
        :param source_comment: -
        :type source_comment: -
        :returns: True on success, False on failure
        :rtype: bool
        """
        logging.debug('Creating reply')
        reply = self._template.format(
            image_url=url,
            comment_id='{comment_id}',
            submission_id=submission.id
        )
        try:
            if source_comment:
                comment = source_comment.reply(reply)
            else:
                comment = submission.reply(reply)
        except praw.exceptions.APIException as error:
            logging.error('Reddit api error, setting retry flag in database | %s', error)
            self._sql.execute('UPDATE submissions SET retry=1 WHERE id=?', (submission.id,))
            if source_comment:
                self._sql.execute('DELETE FROM messages WHERE id=?', (source_comment.id,))
            self._sql_connection.commit()
            return False
        except Exception as error:
            logging.error('Cannot reply, skipping submission | %s', error)
            return False
        logging.debug('Editing comment with remove link')
        comment.edit(reply.format(comment_id=comment.id))
        self._sql.execute('UPDATE submissions SET retry=0 WHERE id=?', (submission.id,))
        self._sql_connection.commit()
        return True

    def _process_submission(self, submission, source_comment=None):
        """Generate new image with added title and author, upload to imgur, reply to submission

        :param submission: the reddit submission object
        :type submission: praw.models.Submission
        :param source_comment: the comment that mentioned the bot, reply to this comment.
            If None, reply at top level. (default None)
        :type source_comment: praw.models.Comment, NoneType
        """
        # TODO really need to clean this method up
        # return if author account is deleted
        if not submission.author:
            return
        sub = submission.subreddit.display_name
        # in r/fakehistoryporn, only process upvoted submissions
        score_threshold = 500
        if sub == 'fakehistoryporn' and not source_comment:
            if submission.score < score_threshold:
                logging.debug('Score below %d in subreddit %s, skipping submission',
                              score_threshold, sub)
                return
        # check db if submission was already processed
        author = submission.author.name
        title = submission.title
        url = submission.url
        params1 = (submission.id,)
        params2 = (submission.id, author, title, url)
        self._sql.execute('SELECT * FROM submissions WHERE id=?', params1)
        result = self._sql.fetchone()
        if result:
            db_id, _, _, _, db_imgur_url, db_retry, _ = result
            if db_retry or source_comment:
                if db_imgur_url:
                    logging.info('Submission id:%s found in database with imgur url set, ' +
                                 'trying to create reply', submission.id)
                    self._reply_imgur_url(db_imgur_url, submission, source_comment)
                    return
                else:
                    logging.info('Submission id:%s found in database without imgur url set, ',
                                 submission.id)
            else:
                # skip submission
                logging.debug('Submission id:%s found in database, returning', db_id)
                return
        else:
            logging.info('Found new submission subreddit:%s id:%s title:%s',
                         sub, submission.id, title)
            logging.debug('Adding submission to database')
            self._sql.execute('INSERT INTO submissions (id, author, title, url) VALUES ' +
                              '(?, ?, ?, ?)', params2)
            self._sql_connection.commit()
        # in r/boottoobig, only process submission with a rhyme in the title
        boot = sub == 'boottoobig'
        if boot and not source_comment:
            triggers = [',', ';', 'roses']
            if not any(t in title.lower() for t in triggers):
                logging.info('Title is probably not part of rhyme, skipping submission')
                return
        if url.endswith('.gif') or url.endswith('.gifv'):
            logging.info('Image is animated gif, skipping submission')
            return
        logging.debug('Trying to download image from %s', url)
        try:
            response = requests.get(url)
            img = Image.open(BytesIO(response.content))
        except OSError as error:
            logging.warning('Converting to image failed, trying with <url>.jpg | %s', error)
            try:
                response = requests.get(url + '.jpg')
                img = Image.open(BytesIO(response.content))
            except OSError as error:
                logging.error('Converting to image failed, skipping submission | %s', error)
                return
        image = RedditImage(img)
        logging.debug('Adding title and author')
        image.add_title(title, boot)
        image.add_author(submission.author.name)
        logging.debug('Trying to upload new image')
        imgur_config = {
            'album': None,
            'name': submission.id,
            'title': title,
            'description': submission.shortlink
        }
        try:
            imgur_url = image.upload(self._imgur, imgur_config)
        except ImgurClientRateLimitError as rate_error:
            logging.error('Imgur ratelimit error, setting retry flag in database | %s', rate_error)
            self._sql.execute('UPDATE submissions SET retry=1 WHERE id=?', (submission.id,))
            if source_comment:
                self._sql.execute('DELETE FROM messages WHERE id=?', (source_comment.id,))
            self._sql_connection.commit()
            return
        if not imgur_url:
            logging.error('Cannot upload new image, skipping submission')
            return
        params = (imgur_url, submission.id)
        self._sql.execute('UPDATE submissions SET imgur_url=? WHERE id=?', params)
        if not self._reply_imgur_url(imgur_url, submission, source_comment):
            return
        logging.info('Successfully processed submission')

    def _process_remove_message(self, message):
        """Remove comment referenced in message body

        :param message: the remove message
        :type message: praw.models.Message
        """
        comment_id = message.body
        logging.info('Found new remove message id:%s', comment_id)
        logging.debug('Trying to remove comment')
        try:
            comment = self._reddit.comment(comment_id)
            submission_author = comment.submission.author.name
            message_author = message.author.name
            if (message_author == submission_author or
                    message_author == __author__):
                comment.delete()
                logging.info('Successfully deleted comment')
            else:
                logging.info('Authors don\'t match, comment not removed')
        except Exception as error:
            logging.error('Cannot remove comment | %s', error)
        finally:
            message.mark_read()

    def _process_feedback_message(self, message):
        """Forward message to creator

        :param message: the feedback message
        :type message: praw.models.Message
        """
        message_author = message.author.name
        logging.info('Found new feedback message from %s', message_author)
        subject = 'TitleToImageBot feedback from {}'.format(message_author)
        body = 'Subject: {}\n\nBody: {}'.format(message.subject, message.body)
        self._reddit.redditor(__author__).message(subject, body)
        message.mark_read()
        logging.info('Forwarded message to author')

    def _process_message(self, message):
        """Process given message (remove, feedback, mark good/bad bot as read)

        :param message: the inbox message, comment reply or username mention
        :type message: praw.models.Message, praw.models.Comment
        """
        if not message.author:
            return
        # check db if message was already processed
        author = message.author.name
        subject = message.subject.lower()
        body = message.body.lower()
        params1 = (message.id,)
        params2 = (message.id, author, subject, body)
        self._sql.execute('SELECT EXISTS(SELECT 1 FROM messages WHERE id=? LIMIT 1)', params1)
        if self._sql.fetchone()[0]:
            logging.debug('Message %s found in database, returning', params1[0])
            return
        logging.debug('Message: %s | %s', subject, body)
        logging.debug('Adding message to database')
        self._sql.execute('INSERT INTO messages (id, author, subject, body) VALUES (?, ?, ?, ?)',
                          params2)
        self._sql_connection.commit()
        # check if message was sent, instead of received
        if author == self._reddit.user.me().name:
            logging.debug('Message was sent, returning')
            return
        # process message
        if subject == 'username mention' and isinstance(message, praw.models.Comment):
            self._process_submission(message.submission, message)
            message.mark_read()
        elif subject == 'remove':
            self._process_remove_message(message)
        elif subject.startswith('feedback'):
            self._process_feedback_message(message)
        elif 'good bot' in body and len(body) < 12:
            logging.info('Good bot message or comment reply found, marking as read')
            message.mark_read()
        elif 'bad bot' in body and len(body) < 12:
            logging.info('Bad bot message or comment reply found, marking as read')
            message.mark_read()

    def run(self, limit):
        """Run the bot

        Process new submissions and messages

        :param limit: amount of submissions/messages to process
        :type limit: int
        """
        logging.debug('Processing last %s submissions...', limit)
        for submission in self._subreddit.new(limit=limit):
            self._process_submission(submission)
        logging.debug('Processing last %s messages, comment replies or username mentions...', limit)
        for message in self._reddit.inbox.all(limit=limit):
            self._process_message(message)


def _setup_logging(level):
    """Setup the root logger

    logs to stdout and to daily log files in ./log/

    :param level: the logging level (e.g. logging.WARNING)
    :type level: int
    """
    console_handler = logging.StreamHandler()
    file_handler = TimedRotatingFileHandler('./log/titletoimagebot.log',
                                            when='midnight', interval=1)
    file_handler.suffix = '%Y-%m-%d'
    module_loggers = ['requests', 'urllib3', 'prawcore', 'PIL.Image', 'PIL.PngImagePlugin']
    for logger in module_loggers:
        logging.getLogger(logger).setLevel(logging.ERROR)
    logging.basicConfig(format='%(asctime)s %(levelname)s %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S',
                        level=level,
                        handlers=[console_handler, file_handler])

def _handle_exception(exc_type, exc_value, exc_traceback):
    """Log unhandled exceptions (see https://stackoverflow.com/a/16993115)"""
    # Don't log ctrl+c
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logging.critical('Unhandled exception:\n', exc_info=(exc_type, exc_value, exc_traceback))

def main():
    """Main function

    Usage: ./titletoimagebot.py [-h] limit interval

    e.g. './titletoimagebot 10 60' will process the last 10 submissions/messages every 60 seconds.
    """
    _setup_logging(logging.INFO)
    sys.excepthook = _handle_exception
    parser = argparse.ArgumentParser()
    parser.add_argument('limit', help='amount of submissions/messages to process each cycle',
                        type=int)
    parser.add_argument('interval', help='time (in seconds) to wait between cycles', type=int)
    args = parser.parse_args()
    logging.debug('Initializing bot')
    with open('subreddits.json') as subreddits_file:
        sub = '+'.join(json.load(subreddits_file))
    bot = TitleToImageBot(sub)
    logging.info('Bot initialized')
    while True:
        try:
            logging.debug('Running bot')
            bot.run(args.limit)
            logging.debug('Bot finished, restarting in %s seconds', args.interval)
        except (requests.exceptions.ReadTimeout,
                requests.exceptions.ConnectionError,
                ResponseException,
                RequestException):
            logging.error('Reddit api timed out, restarting')
            continue
        time.sleep(args.interval)

if __name__ == '__main__':
    main()
