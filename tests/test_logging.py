import io
import logging

from rolfbot.logging_setup import configure_logging


def test_redacts_formatted_arguments_and_both_credentials():
    output = io.StringIO()
    secrets = ("synthetic-openai-value", "synthetic-discord-value")
    logger = configure_logging("DEBUG", secrets=secrets, stream=output)
    logger.info("key=%s token=%r", *secrets)
    text = output.getvalue()
    assert all(secret not in text for secret in secrets)
    assert text.count("[REDACTED]") == 2
    assert "level=INFO logger=rolfbot" in text


def test_suppresses_exception_details_and_stack_even_at_debug():
    output = io.StringIO()
    logger = configure_logging("DEBUG", stream=output)
    try:
        raise RuntimeError("private prompt and an unconfigured secret")
    except RuntimeError:
        logger.exception("event=failed", stack_info=True)
    text = output.getvalue()
    assert "private prompt" not in text
    assert "unconfigured secret" not in text
    assert "Traceback" not in text
    assert "exception_details=omitted" in text
    assert len(text.splitlines()) == 1


def test_redacts_raw_and_escaped_invalid_credentials():
    output = io.StringIO()
    secret = " synthetic\ncredential "
    logger = configure_logging(secrets=[secret], stream=output)
    logger.warning("value=%s repr=%r stripped=%s", secret, secret, secret.strip())
    text = output.getvalue()
    assert "synthetic" not in text
    assert text.count("[REDACTED]") == 3
    assert len(text.splitlines()) == 1


def test_child_logs_are_redacted_and_reconfiguration_does_not_duplicate():
    output = io.StringIO()
    configure_logging(secrets=["synthetic-secret"], stream=output)
    configure_logging("WARNING", secrets=["synthetic-secret"], stream=output)
    logger = logging.getLogger("rolfbot.future")
    logger.info("hidden")
    logger.warning("value=synthetic-secret")
    assert output.getvalue().count("value=[REDACTED]") == 1
    assert "hidden" not in output.getvalue()


def test_bad_log_format_cannot_dump_raw_arguments(capsys):
    output = io.StringIO()
    logger = configure_logging(stream=output)
    logger.error("number=%d", "private-message")
    assert "log_format_error" in output.getvalue()
    assert "private-message" not in output.getvalue() + capsys.readouterr().err


def test_cached_exception_text_from_other_handler_is_not_emitted():
    output = io.StringIO()
    logger = configure_logging(stream=output)
    record = logging.LogRecord("rolfbot", logging.ERROR, "", 0, "event=failed", (), None)
    record.exc_text = "sensitive cached traceback"
    logger.handle(record)
    assert "sensitive" not in output.getvalue()
    assert record.exc_text == "sensitive cached traceback"
