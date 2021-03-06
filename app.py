import os

from flask import Flask, render_template, request, flash, redirect, session, g
from flask_debugtoolbar import DebugToolbarExtension
from sqlalchemy.exc import IntegrityError

from forms import UserAddForm, LoginForm, MessageForm, UserUpdateForm, ForValidationForm
from models import db, connect_db, User, Message, Like

CURR_USER_KEY = "curr_user"

app = Flask(__name__)

# Get DB_URI from environ variable (useful for production/testing) or,
# if not set there, use development local db.
app.config['SQLALCHEMY_DATABASE_URI'] = (
    os.environ.get('DATABASE_URL', 'postgresql:///warbler'))

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ECHO'] = True
app.config['DEBUG_TB_INTERCEPT_REDIRECTS'] = True
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', "it's a secret")
toolbar = DebugToolbarExtension(app)

connect_db(app)


##############################################################################
# User signup/login/logout


@app.before_request
def add_user_to_g():
    """If we're logged in, add curr user to Flask global."""

    if CURR_USER_KEY in session:
        g.user = User.query.get(session[CURR_USER_KEY])
        g.form = ForValidationForm()

    else:
        g.user = None


def do_login(user):
    """Log in user."""

    session[CURR_USER_KEY] = user.id


def do_logout():
    """Logout user."""

    if CURR_USER_KEY in session:
        del session[CURR_USER_KEY]


@app.route('/signup', methods=["GET", "POST"])
def signup():
    """Handle user signup.

    Create new user and add to DB. Redirect to home page.

    If form not valid, present form.

    If the there already is a user with that username: flash message
    and re-present form.
    """

    form = UserAddForm()

    if form.validate_on_submit():
        try:
            user = User.signup(
                username=form.username.data,
                password=form.password.data,
                email=form.email.data,
                image_url=form.image_url.data or User.image_url.default.arg,
            )
            db.session.commit()

        except IntegrityError:
            flash("Username already taken", 'danger')
            return render_template('users/signup.html', form=form)

        do_login(user)

        return redirect("/")

    else:
        return render_template('users/signup.html', form=form)


@app.route('/login', methods=["GET", "POST"])
def login():
    """Handle user login."""

    form = LoginForm()

    if form.validate_on_submit():
        user = User.authenticate(form.username.data,
                                 form.password.data)

        if user:
            do_login(user)
            flash(f"Hello, {user.username}!", "success")
            return redirect("/")

        flash("Invalid credentials.", 'danger')

    return render_template('users/login.html', form=form)


@app.route('/logout', methods=["POST"])
def logout():
    """Handle logout of user - deletes user from session and
        redirects to login page."""
    
    form = ForValidationForm()

    if form.validate_on_submit():
        do_logout()
        flash("Successfully logged out", 'success')
        return redirect("/login")    


##############################################################################
# General user routes:

@app.route('/users')
def list_users():
    """Page with listing of users.
    Can take a 'q' param in querystring to search by that username.
    """

    search = request.args.get('q')

    if not search:
        users = User.query.all()
    else:
        users = User.query.filter(User.username.like(f"%{search}%")).all()

    return render_template('users/index.html', users=users)


@app.route('/users/<int:user_id>')
def users_show(user_id):
    """Show user profile."""

    user = User.query.get_or_404(user_id)
    liked_msg_ids = [lm.id for lm in g.user.liked_messages]

    return render_template('users/show.html', user=user, likes=liked_msg_ids)


@app.route('/users/<int:user_id>/following')
def show_following(user_id):
    """Show list of people this user is following."""

    if not g.user:
        flash("Access unauthorized.", "danger")
        return redirect("/")

    user = User.query.get_or_404(user_id)
    return render_template('users/following.html', user=user)


@app.route('/users/<int:user_id>/followers')
def users_followers(user_id):
    """Show list of followers of this user."""

    if not g.user:
        flash("Access unauthorized.", "danger")
        return redirect("/")

    user = User.query.get_or_404(user_id)
    return render_template('users/followers.html', user=user)


@app.route('/users/follow/<int:follow_id>', methods=['POST'])
def add_follow(follow_id):
    """Add a follow for the currently-logged-in user."""

    if not g.user:
        flash("Access unauthorized.", "danger")
        return redirect("/")
    
    form = ForValidationForm()
    if form.validate_on_submit():
        followed_user = User.query.get_or_404(follow_id)
        g.user.following.append(followed_user)
        db.session.commit()

        return redirect(f"/users/{g.user.id}/following")


@app.route('/users/stop-following/<int:follow_id>', methods=['POST'])
def stop_following(follow_id):
    """Have currently-logged-in-user stop following this user."""

    if not g.user:
        flash("Access unauthorized.", "danger")
        return redirect("/")

    form = ForValidationForm()

    if form.validate_on_submit():
        followed_user = User.query.get(follow_id)
        g.user.following.remove(followed_user)
        db.session.commit()

        return redirect(f"/users/{g.user.id}/following")


@app.route('/users/profile', methods=["GET", "POST"])
def profile():
    """Update profile for current user."""

    if not g.user:
        flash("Access unauthorized.", "danger")
        return redirect("/")

    form = UserUpdateForm(obj=g.user)

    if form.validate_on_submit() and not User.authenticate(g.user.username, form.password.data):
        flash("Unauthorized!", 'danger')

    if form.validate_on_submit() and User.authenticate(g.user.username, form.password.data):
        g.user.username = form.username.data
        g.user.email = form.email.data
        g.user.image_url = form.image_url.data or User.image_url.default.arg
        g.user.header_image_url = form.header_image_url.data or User.header_image_url.default.arg
        g.user.bio = form.bio.data

        db.session.commit()
        return redirect(f"/users/{g.user.id}")

    return render_template("users/edit.html", form=form)


@app.route('/users/delete', methods=["POST"])
def delete_user():
    """Delete user."""

    if not g.user:
        flash("Access unauthorized.", "danger")
        return redirect("/")

    form = ForValidationForm()
    if form.validate_on_submit():

        do_logout()

        db.session.delete(g.user)
        db.session.commit()

        return redirect("/signup")


##############################################################################
# Messages routes:


@app.route('/messages/new', methods=["GET", "POST"])
def messages_add():
    """Add a message:

    Show form if GET. If valid, update message and redirect to user page.
    """

    if not g.user:
        flash("Access unauthorized.", "danger")
        return redirect("/")

    form = MessageForm()

    if form.validate_on_submit():
        msg = Message(text=form.text.data)
        g.user.messages.append(msg)
        db.session.commit()

        return redirect(f"/users/{g.user.id}")

    return render_template('messages/new.html', form=form)


@app.route('/messages/<int:message_id>', methods=["GET"])
def messages_show(message_id):
    """Show a message."""

    msg = Message.query.get(message_id)
    liked_msg_ids = [lm.id for lm in g.user.liked_messages]

    return render_template('messages/show.html', message=msg, likes=liked_msg_ids)


@app.route('/messages/<int:message_id>/delete', methods=["POST"])
def messages_destroy(message_id):
    """Delete a message."""

    if not g.user:
        flash("Access unauthorized.", "danger")
        return redirect("/")

    form = ForValidationForm()

    if form.validate_on_submit():
        msg = Message.query.get(message_id)
        db.session.delete(msg)
        db.session.commit()

        return redirect(f"/users/{g.user.id}")
    
    flash("Action unauthorized!", 'danger')
    return redirect("/")


##############################################################################
# Like routes:


def _handle_like_unlike(message_id, user_id):
    """ helper function that handles liking or unliking messages"""

    user_likes_ids = [lm.id for lm in g.user.liked_messages] 
    is_liked = message_id in user_likes_ids 
    msg = Message.query.get_or_404(message_id)

    # TODO create model methods for logic here
    
    if msg.user_id == g.user.id:
        flash("Sorry, you cannot like your own message!", 'danger')
        return redirect(f"/users/{g.user.id}")
        
    if is_liked:
        g.user.liked_messages.remove(msg)
        flash("message unliked!", 'danger')

    else:
        g.user.liked_messages.append(msg)
        flash("message liked!", 'success')

    db.session.commit()


@app.route("/messages/<int:message_id>/like", methods=["POST"])
def handle_message_like_unlike(message_id):
    """ handles the different routes for liking/unliking messages """

    if not g.user:
        flash("Access unauthorized.", "danger")
        return redirect("/")

    form = ForValidationForm()
    # route = request.form["route"]
    referrer = request.referrer
    if form.validate_on_submit():
        _handle_like_unlike(message_id, g.user.id)

        if referrer:
            return redirect(referrer)

        return redirect("/")

@app.route('/users/<int:user_id>/likes')
def show_user_likes(user_id):
    """If user is not logged in, redirect to homepage
        else, render template for list of user-liked messages """

    if not g.user:
            flash("Access unauthorized.", "danger")
            return redirect("/")

    # TODO handle authoriztion for this route
    # TODO be able to see likes of other users when go to their profile
    return render_template('users/likes.html')


##############################################################################
# Homepage and error pages


@app.route('/')
def homepage():
    """Show homepage:

    - anon users: no messages
    - logged in: 100 most recent messages of followed_users
       with stars beside favorited messages
    """
    
    if g.user:
        following_ids = [u.id for u in g.user.following]
        messages = (Message
                    .query
                    .filter((Message.user_id.in_(following_ids)) | (Message.user_id == g.user.id))
                    .order_by(Message.timestamp.desc())
                    .limit(100)
                    .all())

        liked_msg_ids = {lm.id for lm in g.user.liked_messages}

        return render_template('home.html', messages=messages, liked_msg_ids = liked_msg_ids)

    else:
        return render_template('home-anon.html')


##############################################################################
# Turn off all caching in Flask
#   (useful for dev; in production, this kind of stuff is typically
#   handled elsewhere)
#
# https://stackoverflow.com/questions/34066804/disabling-caching-in-flask

@app.after_request
def add_header(response):
    """Add non-caching headers on every request."""

    # https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Cache-Control
    response.cache_control.no_store = True
    return response
