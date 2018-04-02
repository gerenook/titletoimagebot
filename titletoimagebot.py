#!/usr/bin/env python3

"""meh"""

__version__ = '0.6'
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
from prawcore.exceptions import ResponseException, RequestException

import apidata


class RedditImage:
    """RedditImage class

    :param image: the image
    :type image: PIL.Image.Image
    """
    font_file = 'segoeui.ttf'
    font_scale_factor = 20
    textwrap_limit = 43

    def __init__(self, image):
        self._image = image
        self._width, self._height = image.size
        self._font_title = \
            ImageFont.truetype(RedditImage.font_file,
                               self._width // RedditImage.font_scale_factor)
        self._font_author = \
            ImageFont.truetype(RedditImage.font_file,
                               self._width // (RedditImage.font_scale_factor*2))

    def _split_title(self, title):
        """Split title

        Split title without removing delimiter (str.split() can't do this).
        If no delimiter was found, wrap text

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
        if not new[-1]:
            new = new[:-1]
        if delimiter:
            return new
        return textwrap.wrap(title, RedditImage.textwrap_limit)

    def add_title(self, title, split=False):
        """Add title to new whitespace on image

        :param title: the title to add
        :type title: str
        :param split: if True, split title on [',', ';', '.'], else wrap text
        :type split: bool
        """
        line_height = self._font_title.getsize(title)[1]
        if split:
            texts = self._split_title(title)
        else:
            texts = textwrap.wrap(title, RedditImage.textwrap_limit)
        author_height = self._font_author.getsize('/')[1]
        whitespace_height = (line_height * len(texts)) + author_height + 10
        new = Image.new('RGB', (self._width, self._height + whitespace_height), '#fff')
        new.paste(self._image, (0, whitespace_height))
        draw = ImageDraw.Draw(new)
        for i, text in enumerate(texts):
            draw.text((10, i * line_height + author_height), text, '#000', self._font_title)
        self._width, self._height = new.size
        self._image = new

    def add_author(self, author):
        """Add /u/author to top right of image

        :param author: the author to add (without /u/)
        :type author: str
        """
        text = '/u/' + author
        draw = ImageDraw.Draw(self._image)
        size = self._font_author.getsize(text)
        pos = (self._width - (size[0] + 10), 0)
        draw.text(pos, text, '#000', self._font_author)

    def upload(self, imgur):
        """Upload self._image to imgur

        :param imgur: the imgur api client
        :type imgur: imgurpython.client.ImgurClient
        :returns: imgur url if upload successful, else None
        :rtype: str
        """
        path_png = 'temp.png'
        path_jpg = 'temp.jpg'
        self._image.save(path_png)
        self._image.save(path_jpg)
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
        self._sql_connection = sqlite3.connect('database.db')
        self._sql = self._sql_connection.cursor()
        self._reddit = praw.Reddit(**apidata.reddit)
        self._subreddit = self._reddit.subreddit(subreddit)
        self._imgur = ImgurClient(**apidata.imgur)
        self._template = '[Image with added title]({image_url})\n\n' \
                         '---\n\n' \
                         '^^Summon ^^me ^^with ^^/u/titletoimagebot ^^| ' \
                         '^^[remove](https://reddit.com/message/compose/' \
                         '?to=TitleToImageBot&subject=remove&message={comment_id}) ' \
                         '^^\\(for ^^OP\\) ^^| ' \
                         '^^[feedback](https://reddit.com/message/compose/' \
                         '?to=TitleToImageBot&subject=feedback) ^^| ' \
                         '^^[source](https://github.com/gerenook/titletoimagebot/' \
                         'blob/master/titletoimagebot.py)'

    def _reply_imgur_url(self, url, submission, source_comment):
        """doc todo

        :param url: -
        :type url: str
        :param submission: -
        :type submission: -
        :param source_comment: -
        :type source_comment: -
        :returns: True on succes, False on failure
        :rtype: bool
        """
        logging.debug('Creating reply')
        reply = self._template.format(image_url=url, comment_id='{comment_id}')
        try:
            if source_comment:
                comment = source_comment.reply(reply)
            else:
                comment = submission.reply(reply)
        except praw.exceptions.APIException as rate_error:
            logging.error('Ratelimit error, setting retry flag in database | %s', rate_error)
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

    def _process_submission(self, submission, source_comment=None, check_title=False):
        """Generate new image with added title and author, upload to imgur, reply to submission

        :param submission: the reddit submission object
        :type submission: praw.models.Submission
        :param source_comment: the comment that mentioned the bot, reply to this comment.
            If None, reply at top level. (default None)
        :type source_comment: praw.models.Comment, NoneType
        :param check_title: if True, check if title is part of rhyme. (default False)
        :type check_title: bool
        """
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
            if source_comment:
                if db_retry:
                    # imgur url should be in db -> try commenting again
                    logging.info('Submission id:%s from username mention already in db ' +
                                 'with retry flag set, trying to create reply', (submission.id,))
                    self._reply_imgur_url(db_imgur_url, submission, source_comment)
                    return
                else:
                    if db_imgur_url:
                        logging.info('Submission id:%s from username mention already in db, ' +
                                     'using db imgur url', (submission.id,))
                        self._reply_imgur_url(db_imgur_url, submission, source_comment)
                        return
                    else:
                        # edge case, redo everything
                        pass
            else:
                if db_retry:
                    # imgur url should be in db -> try commenting again
                    logging.info('Found submissions id:%s with retry flag in db, ' +
                                 'trying to create reply', submission.id)
                    self._reply_imgur_url(db_imgur_url, submission, source_comment)
                    return
                else:
                    # submission in db -> skip
                    logging.debug('Submission %s found in database, returning', db_id)
                    return
        else:
            logging.info('Found new submission id:%s title:%s', submission.id, title)
            logging.debug('Adding submission to database')
            self._sql.execute('INSERT INTO submissions (id, author, title, url) VALUES ' +
                              '(?, ?, ?, ?)', params2)
            self._sql_connection.commit()
        if check_title:
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
        image = RedditImage(img)
        logging.debug('Adding title and author')
        image.add_title(title, submission.subreddit.display_name == 'boottoobig')
        image.add_author(submission.author.name)
        logging.debug('Trying to upload new image')
        for _ in range(3):
            imgur_url = image.upload(self._imgur)
            if imgur_url:
                break
            logging.warning('Upload failed, retrying')
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
        subject = 'TitleToImageBot feedback from ' + message_author
        body = message.body
        self._reddit.redditor(__author__).message(subject, body)
        message.mark_read()
        logging.info('Forwarded message to author')

    def _process_message(self, message):
        """Process given message (remove, feedback, mark good/bad bot as read)

        :param message: the inbox message, comment reply or username mention
        :type message: praw.models.Message, praw.models.Comment
        """
        # check db if message was already processed
        if not message.author:
            return
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
            self._process_submission(message.submission, message, False)
            message.mark_read()
        elif subject == 'remove':
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

        :param limit: amount of submissions/messages to process
        :type limit: int
        """
        logging.debug('Processing last %s submissions...', limit)
        for submission in self._subreddit.new(limit=limit):
            self._process_submission(submission, check_title=True)
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
    """Log unhandled exceptions (level critical)"""
    # Don't log ctrl+c
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    text = ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    logging.critical('Unhandled exception:\n%s', text)

def main():
    """Main function

    Usage: ./titletoimagebot.py [-h] limit interval
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
            logging.info('Bot finished, restarting in %s seconds', args.interval)
        except (requests.exceptions.ReadTimeout,
                requests.exceptions.ConnectionError,
                ResponseException,
                RequestException):
            logging.error('Reddit api timed out, restarting')
            continue
        time.sleep(args.interval)

if __name__ == '__main__':
    main()
