import time

call_registry = {}
call_counter = 1000


async def register_call(caller_number_str: str) -> int:
    global call_counter
    call_counter += 1
    call_id = call_counter

    call_registry[call_id] = {
        "call_id": call_id,
        "caller_phone": caller_number_str,
        "status": "initiated",
        "start_time": time.time(),
        "end_time": None
    }

    print(f"✅ Registered call {call_id} from {caller_number_str} @ {time.time()}")
    return call_id


async def update_call_status(call_id: int, action: str) -> bool:
    if call_id in call_registry:
        call_registry[call_id]["status"] = action
        if action == "end":
            call_registry[call_id]["end_time"] = time.time()
        print(f"✅ Updated call {call_id} status to {action} @ {time.time()}")
        return True
    return False


def get_call_info(call_id: int) -> dict:
    return call_registry.get(call_id, {})
