"""`bank-transactions create-user`: the password is prompted for, never a command line argument."""

import base64
import io

import pytest

from bank_transactions.cli import main
from bank_transactions.domain.errors import ValidationFailedError

KEY = base64.b64encode(bytes(range(32))).decode()
DSN = "postgresql+psycopg://user:secret-pw@127.0.0.1:5432/bank"
FULL_ENV = {"BT_DSN": DSN, "BT_CIPHER_KEY": KEY}
ARGV = ("create-user", "--email", "  Dana@Example.com ", "--name", "Dana Levi")


class Provisioner:
    def __init__(self, failure=None):
        self.calls = []
        self.failure = failure

    def __call__(self, settings, email, name, password):
        if self.failure:
            raise self.failure
        self.calls.append((email, name, password))


class Prompts:
    """Answers the password prompts in order and remembers what was asked."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked = []

    def __call__(self, prompt):
        self.asked.append(prompt)
        return self.answers.pop(0)


@pytest.fixture
def provisioner():
    return Provisioner()


@pytest.fixture
def run(provisioner):
    def invoke(*argv, env=FULL_ENV, prompts=None, creator=None):
        out, err = io.StringIO(), io.StringIO()
        code = main(
            list(argv),
            environ=env,
            stdout=out,
            stderr=err,
            user_creator=creator or provisioner,
            read_password=prompts or Prompts("s3cret-pw", "s3cret-pw"),
        )
        return code, out.getvalue(), err.getvalue()

    return invoke


def test_the_password_is_prompted_twice_and_passed_to_the_creator(run, provisioner):
    prompts = Prompts("s3cret-pw", "s3cret-pw")

    code, out, err = run(*ARGV, prompts=prompts)

    assert code == 0
    assert provisioner.calls == [("  Dana@Example.com ", "Dana Levi", "s3cret-pw")]
    assert [p.lower() for p in prompts.asked] == ["password: ", "repeat password: "]
    assert out.strip() == "Created user Dana@Example.com."
    assert err == ""


def test_the_password_is_never_echoed(run):
    code, out, err = run(*ARGV, prompts=Prompts("s3cret-pw", "s3cret-pw"))

    assert "s3cret-pw" not in out + err


def test_a_password_argument_is_not_accepted(run, provisioner):
    code, _, err = run(*ARGV, "--password", "s3cret-pw")

    assert code == 2
    assert "unrecognized arguments" in err
    assert provisioner.calls == []


def test_mismatched_passwords_create_nothing(run, provisioner):
    code, out, err = run(*ARGV, prompts=Prompts("one", "two"))

    assert code == 2
    assert "do not match" in err
    assert provisioner.calls == []


@pytest.mark.parametrize("missing", ["BT_DSN", "BT_CIPHER_KEY"])
def test_the_database_and_the_key_are_required_before_any_prompt(run, provisioner, missing):
    prompts = Prompts()
    env = {name: value for name, value in FULL_ENV.items() if name != missing}

    code, _, err = run(*ARGV, env=env, prompts=prompts)

    assert code == 2
    assert missing in err
    assert prompts.asked == []
    assert provisioner.calls == []


def test_name_and_email_are_required(run):
    assert run("create-user", "--email", "a@b.co")[0] == 2
    assert run("create-user", "--name", "Dana")[0] == 2


def test_an_unusable_value_is_a_usage_error_with_the_reason(run):
    failure = ValidationFailedError.single(
        "email", "ALREADY_EXISTS", "a user with that email already exists"
    )

    code, out, err = run(*ARGV, creator=Provisioner(failure))

    assert code == 2
    assert "a user with that email already exists" in err
    assert "Created" not in out


def test_an_unexpected_failure_is_neutral_and_exits_one(run):
    code, out, err = run(*ARGV, creator=Provisioner(RuntimeError(f"boom {DSN} s3cret-pw")))

    assert code == 1
    assert "boom" not in out + err
    assert "s3cret-pw" not in out + err
    assert DSN not in out + err
