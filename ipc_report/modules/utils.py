import logging

def confirm(prompt: str) -> bool:
    while True:
        answer = input(f"{prompt}? (y/N)").lower()
        match answer:
            case "y" | "yes":
                return True
            case "" | "n" | "no":
                return False
            case _:
                logging.error("Invalid input")
                continue
