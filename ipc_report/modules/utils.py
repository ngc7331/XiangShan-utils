import logging
import math

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


def geometric_mean(nums: list[float]):
    product = 1
    for n in nums:
        product *= n
    return math.pow(product, 1/len(nums))
