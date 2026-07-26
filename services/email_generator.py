def generate_sequence(company: str, person_name: str, persona_title: str) -> list[dict]:
    # Real sequence templates (no dummy placeholders like lorem ipsum)
    s1_subject = f"Quick idea to replace your agency workflow at {company}"
    s1_body = (
        f"Hi {person_name},\n\n"
        f"I’m reaching out because we’re building a workflow that replaces large parts of what agencies do — faster iteration, lower overhead, and clearer performance loops.\n\n"
        f"If you’re the right person (as {persona_title}), I can share how teams similar to {company} are moving from agency dependency to an in-house-like system without the headcount.\n\n"
        f"Open to a 10-min chat this week?\n"
    )

    s2_subject = f"Following up — agency replacement for {company}"
    s2_body = (
        f"Hi {person_name},\n\n"
        f"Just looping back. If {company} is currently working with an agency, we can usually find quick wins in:\n"
        f"- speed of testing\n"
        f"- creative iteration\n"
        f"- performance feedback loops\n\n"
        f"If it’s helpful, tell me what channel matters most right now (Meta, Google, landing pages), and I’ll tailor the 10-min walkthrough.\n"
    )

    s3_subject = f"Last note, {person_name}"
    s3_body = (
        f"Hi {person_name},\n\n"
        f"I’ll close the loop here — if replacing or reducing agency dependency isn’t a priority for {company} right now, no worries.\n\n"
        f"If it becomes relevant later, I’m happy to share a quick breakdown of where we typically outperform agencies.\n"
    )

    return [
        {"step": 1, "subject": s1_subject, "body": s1_body},
        {"step": 2, "subject": s2_subject, "body": s2_body},
        {"step": 3, "subject": s3_subject, "body": s3_body},
    ]
