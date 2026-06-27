from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from Utils import performance_timer
from Database import DatabaseClient

@performance_timer
async def update_users_job():
    from API.MSGraphAPI.Users import get_users
    from Database.Models import User, Access, Application

    users = await get_users()
    client = DatabaseClient()

    async with client.session("powerplatform") as session:

        apps_result = await session.execute(select(Application.application_id))
        application_ids = [row[0] for row in apps_result.all()]

        for graph_user in users.value:

            # === 1. Upsert User ===
            stmt_user = (
                insert(User)
                .values(
                    user_id=graph_user.id,
                    display_name=graph_user.display_name,
                    given_name=graph_user.given_name,
                    surname=graph_user.surname,
                    user_principal_name=graph_user.user_principal_name,
                    account_enabled=graph_user.account_enabled,
                    mail=graph_user.mail,
                    mobile_phone=graph_user.mobile_phone,
                    city=graph_user.city,
                    country=graph_user.country,
                    department=graph_user.department,
                    job_title=graph_user.job_title,
                    employee_id=graph_user.employee_id,
                    employee_hire_date=graph_user.employee_hire_date,
                    created_date_time=graph_user.created_date_time,
                )
                .on_conflict_do_update(
                    index_elements=['user_id'],
                    set_={
                        'display_name': graph_user.display_name,
                        'given_name': graph_user.given_name,
                        'surname': graph_user.surname,
                        'user_principal_name': graph_user.user_principal_name,
                        'account_enabled': graph_user.account_enabled,
                        'mail': graph_user.mail,
                        'mobile_phone': graph_user.mobile_phone,
                        'city': graph_user.city,
                        'country': graph_user.country,
                        'department': graph_user.department,
                        'job_title': graph_user.job_title,
                        'employee_id': graph_user.employee_id,
                        'employee_hire_date': graph_user.employee_hire_date,
                        'created_date_time': graph_user.created_date_time,
                    }
                )
                .returning(User.user_id)
            )

            result = await session.execute(stmt_user)
            db_user_id = result.scalar_one()

            # === 2. ApplicationAccess ===
            if application_ids:
                access_rows = [
                    {
                        "user_id": db_user_id,
                        "application_id": app_id,
                        "main_access": False,
                    }
                    for app_id in application_ids
                ]

                stmt_access = (
                    insert(Access)
                    .values(access_rows)
                    .on_conflict_do_nothing()
                )

                await session.execute(stmt_access)

        await session.commit()



if __name__ == "__main__":
    import asyncio
    asyncio.run(update_users_job())
