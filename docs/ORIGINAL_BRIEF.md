I want to build an application. My security team currently relies on Excel Spreadsheets to manage
  their incidents. I don't like using it, I find it clunky and not user friendly nor easy to
  navigate. It is also difficult to know who is working on what element and how each IOC or TTP
  relate to each other.

  For this new application, it should have the following features and functionality:
  • Authentication: with the user requiring email, name, password and team. Additional security
  components MUST include MFA and Backup Tokens.
  • Findings: Means of inputting three different types of data: Intelligence, Findings/Evidence,
  Potentials. Findings/Evidence and Potential MUST have a time field - time when the event occured.
  Input time will be automatic for all input types. As an IOC is inputted, the system should be able
  to identify what type of IOC it is - IP address, process, file, path, hash, email, domain, phone
  number, alias (or any others I have missed)
    • There should be a way of marking when each one occured and on which host/user. We also need to
  add optional comments.
  • Timeline: chronological events for both Occurance Time and Inputted Times.
  • Systems of interest: include hostname, IP address, primary user account, public IP address (if
  applicable), purpose, data-type hosted. And based on collated information, the systems should be
  prioritized. And ONE system can be marked as Patient Zero.
  • Other features: There should be some automated assessment in the background of the application
  which prioritises the IOCs and TTPs based on information elsewhere and intelligence collected. it
  needs to update in real time whilst people are using it and be able to notify people of someone
  else is working on the same or similar IOC/TTP.
  • Reporting: ability to output an Executive Summary based on all information provided. 
  • Please suggest any other features or functionality which may benefit the application.- 