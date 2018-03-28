#!/usr/bin/env python3

"""meh"""

__version__ = '0.5'
__author__ = 'gerenook'

import argparse
import logging
import sqlite3
import sys
import textwrap
import time
import traceback
from io import BytesIO
from logging.handlers import TimedRotatingFileHandler
from os import remove

import praw
import requests
from imgurpython import ImgurClient
from imgurpython.helpers.error import (ImgurClientError,
                                       ImgurClientRateLimitError)
from PIL import Image, ImageDraw, ImageFont
from prawcore.exceptions import ResponseException

import apidata


class Meme:
    """Meme class

    :param image: the image (meme)
    :type image: PIL.Image.Image
    """
    font_file = 'segoeui.ttf'
    font_scale_factor = 20

    def __init__(self, image):
        self._meme = image
        self._width, self._height = image.size

    def _split_title(self, title):
        """Split title

        Split title without removing delimiter (str.split() can't do this).
        If no delimiter was found, wrap text at 45 characters

        :param title: the title to split
        :type title: str
        :returns: split title
        :rtype: list
        """
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
        if delimiter:
            return new
        return textwrap.wrap(title, 45)

    def add_title(self, title):
        """Add title to new whitespace on meme

        :param title: the title to add
        :type title: str
        """
        font = ImageFont.truetype(Meme.font_file, self._width // Meme.font_scale_factor)
        line_height = font.getsize(title)[1]
        texts = self._split_title(title)
        whitespace_height = (line_height * len(texts)) + 10
        new = Image.new('RGB', (self._width, self._height + whitespace_height), '#fff')
        new.paste(self._meme, (0, whitespace_height))
        draw = ImageDraw.Draw(new)
        for i, text in enumerate(texts):
            draw.text((10, i * line_height), text, '#000', font)
        self._width, self._height = new.size
        self._meme = new

    def add_author(self, author):
        """Add /u/author to top right of image

        :param author: the author to add (without /u/)
        :type author: str
        """
        font = ImageFont.truetype(Meme.font_file, self._width // (Meme.font_scale_factor*2))
        text = '/u/' + author
        draw = ImageDraw.Draw(self._meme)
        size = font.getsize(text)
        pos = (self._width - (size[0] + 10), 0)
        draw.text(pos, text, '#000', font)

    def upload(self, imgur):
        """Upload self._meme to imgur

        :param imgur: the imgur api client
        :type imgur: imgurpython.client.ImgurClient
        :returns: imgur url if upload successful, else None
        :rtype: str
        """
        path_png = 'temp.png'
        path_jpg = 'temp.jpg'
        self._meme.save(path_png)
        self._meme.save(path_jpg)
        try:
            response = imgur.upload_from_path(path_png)
        except (ImgurClientError, ImgurClientRateLimitError) as error:
            logging.warning('png upload failed, trying jpg | %s', error)
            try:
                response = imgur.upload_from_path(path_jpg)
            except (ImgurClientError, ImgurClientRateLimitError) as error:
                logging.error('jpg upload failed, returning | %s', error)
                return None
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
        if subreddit != 'boottoobig':
            raise NotImplementedError
        self._sql_connection = sqlite3.connect('database.db')
        self._sql = self._sql_connection.cursor()
        self._reddit = praw.Reddit(**apidata.reddit)
        self._subreddit = self._reddit.subreddit(subreddit)
        self._imgur = ImgurClient(**apidata.imgur)
        self._template = '[Image with title]({image_url})\n\n' \
                         '---\n\n' \
                         '^^[remove](https://reddit.com/message/compose/' \
                         '?to=TitleToMemeBot&subject=remove&message={comment_id}) ' \
                         '^^\\(for ^^OP\\) ^^| ' \
                         '^^[feedback](https://reddit.com/message/compose/' \
                         '?to=TitleToMemeBot&subject=feedback) ^^| ' \
                         '^^[source](https://github.com/gerenook/titletomemebot/' \
                         'blob/master/titletomemebot.py)'

    def _process_submission(self, submission):
        """Generate new image with added title and author, upload to imgur, reply to submission

        :param submission: the reddit submission object
        :type submission: praw.models.reddit.submission.Submission
        """
        # check db if submission was already processed
        author = submission.author.name
        title = submission.title
        url = submission.url
        params1 = (submission.id,)
        params2 = (submission.id, author, title, url)
        self._sql.execute('SELECT EXISTS(SELECT 1 FROM submissions WHERE id=? LIMIT 1)', params1)
        if self._sql.fetchone()[0]:
            logging.debug('Submission %s found in database, returning', params1[0])
            return
        logging.info('Found new submission id:%s title:%s', submission.id, title)
        logging.debug('Adding submission to database')
        self._sql.execute('INSERT INTO submissions (id, author, title, url) VALUES (?, ?, ?, ?)',
                          params2)
        self._sql_connection.commit()
        triggers = [',', '.', ';', 'roses']
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
        meme = Meme(img)
        logging.debug('Adding title and author')
        meme.add_title(title)
        meme.add_author(submission.author.name)
        logging.debug('Trying to upload new image')
        for _ in range(3):
            url = meme.upload(self._imgur)
            if url:
                break
            logging.warning('Upload failed, retrying')
        if not url:
            logging.error('Cannot upload new image, skipping submission')
            return
        logging.debug('Creating reply')
        reply = self._template.format(image_url=url, comment_id='{comment_id}')
        try:
            comment = submission.reply(reply)
        except Exception as error:
            logging.error('Cannot reply, skipping submission')
            return
        logging.debug('Editing comment with remove link')
        comment.edit(reply.format(comment_id=comment.id))
        logging.info('Successfully processed submission')

    def _process_remove_message(self, message):
        """Remove comment referenced in message body

        :param message: the remove message
        :type message: praw.models.reddit.message.Message
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
        :type message: praw.models.reddit.message.Message
        """
        message_author = message.author.name
        logging.info('Found new feedback message from %s', message_author)
        subject = 'TitleToMemeBot feedback from ' + message_author
        body = message.body
        self._reddit.redditor(__author__).message(subject, body)
        message.mark_read()
        logging.info('Forwarded message to author')

    def _process_message(self, message):
        """Process given message (remove, feedback, mark good/bad bot as read)

        :param message: the inbox message
        :type message: praw.models.reddit.message.Message
        """
        # check db if submission was already processed
        author = message.author.name
        subject = message.subject.lower()
        body = message.body.lower()
        params1 = (message.id,)
        params2 = (message.id, author, subject, body)
        self._sql.execute('SELECT EXISTS(SELECT 1 FROM messages WHERE id=? LIMIT 1)', params1)
        if self._sql.fetchone()[0]:
            logging.debug('Message %s found in database, returning', params1[0])
            return
        logging.debug('New message: %s | %s', subject, body)
        logging.debug('Adding message to database')
        self._sql.execute('INSERT INTO messages (id, author, subject, body) VALUES (?, ?, ?, ?)',
                          params2)
        self._sql_connection.commit()
        # check if message was sent, instead of received
        if author == self._reddit.user.me().name:
            logging.debug('Message was sent, returning')
            return
        if subject == 'remove':
            self._process_remove_message(message)
        elif subject == 'feedback':
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

        :param limit: amount of submission/messages to process
        :type limit: int
        """
        logging.debug('Processing last %s submissions...', limit)
        for submission in self._subreddit.new(limit=limit):
            self._process_submission(submission)
        logging.debug('Processing last %s messages and/or comment replies...', limit)
        for message in self._reddit.inbox.all(limit=limit):
            self._process_message(message)


def _setup_logging(level):
    """Setup the root logger

    logs to stdout and to daily log files in ./log/

    :param level: the logging level (e.g. logging.WARNING)
    :type level: int
    """
    console_handler = logging.StreamHandler()
    file_handler = TimedRotatingFileHandler('./log/titletomemebot.log', when='midnight', interval=1)
    file_handler.suffix = '%Y-%m-%d'
    module_loggers = ['requests', 'urllib3', 'prawcore', 'PIL.Image', 'PIL.PngImagePlugin']
    for logger in module_loggers:
        logging.getLogger(logger).setLevel(logging.ERROR)
    logging.basicConfig(format='%(asctime)s %(levelname)s %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S',
                        level=level,
                        handlers=[console_handler, file_handler])

def _handle_exception(exc_type, exc_value, exc_traceback):
    """Log unhandled exceptions (level critical)"""
    # Don't log ctrl+c
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    text = ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    logging.critical('Unhandled exception:\n%s', text)

def main():
    """Main function

    Usage: ./titletomemebot.py [-h] limit interval
    """
    _setup_logging(logging.INFO)
    sys.excepthook = _handle_exception
    parser = argparse.ArgumentParser()
    parser.add_argument('limit', help='amount of submissions/messages to process each cycle',
                        type=int)
    parser.add_argument('interval', help='time (in seconds) to wait between cycles', type=int)
    args = parser.parse_args()
    logging.debug('Initializing bot')
    bot = TitleToImageBot('boottoobig')
    while True:
        try:
            logging.info('Running bot')
            bot.run(args.limit)
            logging.info('bot finished, restarting in %s seconds', args.interval)
        except (requests.exceptions.ReadTimeout,
                requests.exceptions.ConnectionError,
                ResponseException):
            logging.error('Reddit api timed out, restarting')
            continue
        time.sleep(args.interval)

if __name__ == '__main__':
    main()
