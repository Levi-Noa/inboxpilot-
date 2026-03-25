# AI Solution Engineer - Technical Assessment
## Take-Home Exercise: Email Response Agent

**Position:** AI Solution Engineer – Corporate Business Applications @ WalkMe  
**Language:** JavaScript or Python (your choice)

---

## Objective

Build an AI agent that helps users respond to emails. The agent should use an LLM to orchestrate tools for searching Gmail, generating responses, and sending replies based on user interaction.

---

## Functional Requirements

Your agent must implement the following capabilities using an **agent-with-tools architecture**:

- [ ] Accept user input for an email subject to search for
- [ ] Search Gmail for messages matching that subject
- [ ] Display the email content to the user (from, subject, body)
- [ ] Generate a suggested reply using OpenAI API
- [ ] Show the suggested reply to the user
- [ ] Wait for user confirmation (approve / reject / modify)
- [ ] Send the reply if approved
- [ ] Handle errors gracefully (email not found, API failures, etc.)

> **Note:** The agent should determine *when* and *how* to use tools based on the conversation flow.

---

## Technical Requirements

- Connect to Gmail for email operations (search and send)
- Use OpenAI API for response generation
- Code must be runnable by the evaluator using their own Gmail credentials
- Include error handling and user feedback
- **Do NOT include** your actual Gmail credentials or OpenAI API key in the submission

---

## Example Interaction

```
User:  Can you help me respond to the email about the project proposal follow-up?

Agent: I'll search for that email.

Agent: I found an email from john@example.com sent on Jan 15, 2025:
       Subject: Project proposal follow-up
       From: john@example.com
       Body: Hi, I wanted to follow up on the proposal we sent last week...

Agent: Let me draft a response for you.

Agent: Here's my suggested reply:
       ---
       Hi John,
       Thank you for following up. I've reviewed the proposal and have a few questions...
       ---
       Would you like me to send this reply?

User:  yes

Agent: Reply sent successfully!
```

---

## Deliverables

Submit a ZIP file containing:

1. **Source code** – all files needed to run the agent
2. **README.md** with:
   - Setup instructions (dependencies, credentials configuration)
   - How to run the agent
   - Assumptions or design decisions made
3. **Dependencies file** – `package.json` or `requirements.txt`

> ⚠️ README **must** include clear instructions for the evaluator to configure their own credentials.

---

## Submission

Email your solution to:
- `yarden.t@walkme.com`
- `dorin.weil@walkme.com`

Submission formats accepted:
- ZIP file attachment
- Google Drive link
- GitHub repository link

---

## Notes

- You have complete freedom in choosing your approach, frameworks, and libraries
- Document any assumptions in your README
- **Quality over complexity** – a simple, working solution is better than an incomplete ambitious one
- OpenAI API key will be provided by WalkMe (for personal use in this assessment only)
