from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('invoice_app', '0021_invoice_email_recipients_templates'),
    ]

    operations = [
        migrations.AddField(
            model_name='invoiceemailtemplate',
            name='category',
            field=models.CharField(
                choices=[('common', 'Common'), ('customer', 'Customer'), ('salary', 'Salary')],
                db_index=True,
                default='customer',
                max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name='invoiceemailtemplate',
            name='text',
            field=models.TextField(
                help_text='Optional placeholders depend on use: invoice ({invoice_number}, '
                          '{customer_name}, {customer_brand}, {invoice_date}, {invoice_month}, '
                          '{invoice_year}, {grand_total}, {month}, {year}); salary '
                          '({employee_name}, {employee_id}, {salary_month}, {salary_year}, '
                          '{month}, {year}).'
            ),
        ),
        migrations.AlterModelOptions(
            name='invoiceemailtemplate',
            options={
                'ordering': ['category', 'kind', 'name', 'id'],
                'verbose_name': 'Email template',
                'verbose_name_plural': 'Email templates',
            },
        ),
        migrations.AddField(
            model_name='invoiceemaildefaults',
            name='salary_subject_template',
            field=models.ForeignKey(
                blank=True,
                limit_choices_to={'kind': 'subject'},
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+',
                to='invoice_app.invoiceemailtemplate',
                verbose_name='Salary slip subject default',
            ),
        ),
        migrations.AddField(
            model_name='invoiceemaildefaults',
            name='salary_body_template',
            field=models.ForeignKey(
                blank=True,
                limit_choices_to={'kind': 'body'},
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+',
                to='invoice_app.invoiceemailtemplate',
                verbose_name='Salary slip body default',
            ),
        ),
        migrations.AlterModelOptions(
            name='invoiceemaildefaults',
            options={
                'verbose_name': 'Email template defaults',
                'verbose_name_plural': 'Email template defaults',
            },
        ),
    ]
